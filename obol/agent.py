"""The Obol agent loop.

One pass = pick a provider (bandit), pay it (Arc adapter, x402), evaluate what came
back, feed the score to the bandit, append to the ledger, and periodically publish the
provider's running edge on-chain. Repeat until the budget runs dry, the call cap is hit,
or every provider has been retired as not-worth-buying.

The whole point of the project is in `step`: a real decision, a real payment, a real
update -- not a scripted demo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .allocator import ValueBandit
from .arc.adapter import ArcAdapter
from .config import Provider, Settings
from .eval.evaluator import evaluate
from .reputation.ledger import Ledger, LedgerEntry
from .state import ArmState, load_state, reconcile, save_state


@dataclass
class Agent:
    providers: list[Provider]
    adapter: ArcAdapter
    settings: Settings
    ledger: Ledger = field(default_factory=Ledger)
    on_event: Callable[[dict], None] | None = None
    rep_write_every: int = 5  # publish edge on-chain every N calls per provider
    # Where cross-run memory lives. None = no persistence (simulation, tests).
    state_path: Path | None = None

    def __post_init__(self) -> None:
        self.bandit = ValueBandit(
            self.providers,
            min_edge=self.settings.min_edge,
            cutoff_confidence=self.settings.cutoff_confidence,
            explore_rounds=self.settings.explore_rounds,
            decay=self.settings.decay,
            epsilon=self.settings.epsilon,
        )
        self._by_id = {p.id: p for p in self.providers}
        self._call_count = 0
        self._rep_counter: dict[str, int] = {}
        self._last_evidence: dict[str, str] = {}
        self._written: dict[str, int] = {}  # provider -> calls at last on-chain write (-1 = retired)
        self._run_calls: dict[str, int] = {}  # provider -> calls made in THIS run
        self._run_spend: dict[str, float] = {}

    def load_history(self) -> None:
        """Start every seller from its accumulated record, not a blank prior.

        Reads the local state file and the agent's own on-chain record (the same public
        registry any other agent would check before paying), reconciles them, and seeds the
        bandit. Then applies the cut-off rule to that cumulative evidence: a seller that has
        failed enough times across runs is retired on-chain now, with its evidence."""
        local = load_state(self.state_path) if self.state_path else {}
        for pid in self.bandit.arms:
            chain = self.adapter.read_reputation(pid)
            st = reconcile(local.get(pid), chain)
            if st is None:
                continue
            self.bandit.seed(pid, st.alpha, st.beta, st.calls, st.seen, st.delivered,
                             st.spend_usdc, retired=st.retired)
            if st.evidence:
                self._last_evidence[pid] = st.evidence
            self._written[pid] = -1 if st.retired else st.calls
            self._emit("history", provider=self._by_id[pid].name, calls=st.calls,
                       score_bps=self.bandit.quality_bps(pid), retired=st.retired)
        for pid, arm in self.bandit.arms.items():
            if arm.retired or arm.calls == 0 or not self.bandit.should_retire(pid):
                continue
            arm.retired = True
            self._retire_on_chain(pid, reason=f"failed delivery on {arm.seen - arm.delivered} "
                                              f"of {arm.seen} calls across runs")

    def _retire_on_chain(self, pid: str, reason: str) -> None:
        arm = self.bandit.arms[pid]
        rc = self.adapter.write_reputation(pid, self.bandit.quality_bps(pid), calls=arm.calls,
                                           retire=True, evidence=self._last_evidence.get(pid, ""))
        self._written[pid] = -1  # retired: final
        self._emit("retire", provider=self._by_id[pid].name, reason=reason,
                   tx=rc.tx_hash, explorer=rc.explorer_url)

    def save_history(self) -> None:
        if not self.state_path:
            return
        state = {}
        for pid, a in self.bandit.arms.items():
            if a.calls == 0:
                continue
            # A provider parked this run because WE couldn't pay is not retired; only an
            # on-chain retirement is persisted as retired.
            state[pid] = ArmState(alpha=a.alpha, beta=a.beta, calls=a.calls, seen=a.seen,
                                  delivered=a.delivered, spend_usdc=round(a.spend_usdc, 6),
                                  evidence=self._last_evidence.get(pid, ""),
                                  retired=self._written.get(pid) == -1)
        save_state(state, self.state_path)

    def _emit(self, kind: str, **data) -> None:
        if self.on_event:
            self.on_event({"kind": kind, "ts": time.time(), **data})

    def step(self, request: dict) -> bool:
        """Run one buy. Returns False when the agent decides there's nothing left to buy."""
        budget = self.adapter.balance_usdc()
        arm = self.bandit.select(budget)
        if arm is None:
            return False

        provider = self._by_id[arm.provider_id]
        result = self.adapter.pay_and_fetch(provider, request)
        if result.refused or (not result.receipt.settled and result.body is None):
            # WE could not pay (budget, caps, funds, policy) -- not the seller's fault.
            # Park this provider for the rest of the run (local only, nothing on-chain);
            # stop when nothing payable is left.
            self.bandit.arms[provider.id].retired = True
            self._emit("skip", provider=provider.name, reason=result.evidence or "unpaid")
            return any(not a.retired for a in self.bandit.arms.values())

        # Paid call, or a seller-side failure the seller did not charge for (e.g. HTTP 500):
        # both are real observations of the seller's delivery.
        quality = evaluate(request, result.body, self.settings.eval_model,
                           provider=provider, latency_ms=result.latency_ms)
        delivered = result.body is not None and result.body.get("data") is not None
        if result.evidence and (result.receipt.settled or provider.id not in self._last_evidence
                                or not self._last_evidence[provider.id].startswith(("0x", "gateway:"))):
            self._last_evidence[provider.id] = result.evidence
        self.bandit.observe(arm.provider_id, quality, result.receipt.amount_usdc, delivered=delivered)
        self._call_count += 1
        self._run_calls[provider.id] = self._run_calls.get(provider.id, 0) + 1
        self._run_spend[provider.id] = self._run_spend.get(provider.id, 0.0) + result.receipt.amount_usdc

        entry = LedgerEntry(
            ts=time.time(),
            provider_id=provider.id,
            provider_name=provider.name,
            paid_usdc=result.receipt.amount_usdc,
            quality=round(quality, 4),
            edge=round(self.bandit.arms[provider.id].mean_edge, 4),
            tx_hash=result.receipt.tx_hash,
            explorer_url=result.receipt.explorer_url,
            delivered=delivered,
        )
        self.ledger.record(entry)
        self._emit(
            "buy",
            provider=provider.name,
            paid=entry.paid_usdc,
            quality=entry.quality,
            edge=entry.edge,
            tx=entry.tx_hash,
            explorer=entry.explorer_url,
            delivered=delivered,
            balance=self.adapter.balance_usdc(),
            retired=self.bandit.arms[provider.id].retired,
        )

        # Periodically publish the running edge on-chain.
        n = self._rep_counter.get(provider.id, 0) + 1
        self._rep_counter[provider.id] = n
        if n % self.rep_write_every == 0:
            score = self.bandit.quality_bps(provider.id)
            calls = self.bandit.arms[provider.id].calls
            rc = self.adapter.write_reputation(provider.id, score, calls=calls,
                                               evidence=self._last_evidence.get(provider.id, ""))
            self._written[provider.id] = calls
            self._emit("reputation", provider=provider.name,
                       score_bps=score, tx=rc.tx_hash, explorer=rc.explorer_url)

        if self.bandit.arms[provider.id].retired:
            self._retire_on_chain(provider.id, reason="delivery below floor with confidence")
        return True

    def run(self, request_factory: Callable[[], dict] | None = None) -> dict:
        request_factory = request_factory or (lambda: {"query": "default"})
        self._emit("start", budget=self.adapter.balance_usdc(),
                   providers=len(self.providers))
        self.load_history()
        try:
            while self._call_count < self.settings.max_calls:
                if not self.step(request_factory()):
                    break
            self.flush_reputation()
        finally:
            self.save_history()
        return self.summary()

    def flush_reputation(self) -> None:
        """Publish a final score for every provider observed this run whose on-chain record
        is missing or stale, so each seller Obol actually tried ends up recorded."""
        for pid, arm in self.bandit.arms.items():
            last = self._written.get(pid)
            if arm.calls == 0 or last == -1 or last == arm.calls:
                continue
            score = self.bandit.quality_bps(pid)
            rc = self.adapter.write_reputation(pid, score, calls=arm.calls,
                                               evidence=self._last_evidence.get(pid, ""))
            self._written[pid] = arm.calls
            self._emit("reputation", provider=self._by_id[pid].name, score_bps=score,
                       tx=rc.tx_hash, explorer=rc.explorer_url)

    def summary(self) -> dict:
        ranking = self.bandit.ranking()
        return {
            "budget_start": self.settings.budget_usdc,
            "balance_end": self.adapter.balance_usdc(),
            "calls": self._call_count,
            "spend": self.ledger.total_spend(),
            "value": self.ledger.total_value(),
            "value_per_usdc": round(
                self.ledger.total_value() / self.ledger.total_spend(), 4
            ) if self.ledger.total_spend() else 0.0,
            "ranking": [
                {
                    "provider": a.provider_id,
                    "calls": a.calls,  # cumulative, as published on-chain
                    "run_calls": self._run_calls.get(a.provider_id, 0),
                    "run_spend": round(self._run_spend.get(a.provider_id, 0.0), 4),
                    "spend": round(a.spend_usdc, 4),
                    "mean_quality": round(a.mean_quality, 4),
                    "edge": round(a.mean_edge, 4),
                    "retired": a.retired,
                }
                for a in ranking
            ],
        }
