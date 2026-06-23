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


@dataclass
class Agent:
    providers: list[Provider]
    adapter: ArcAdapter
    settings: Settings
    ledger: Ledger = field(default_factory=Ledger)
    on_event: Callable[[dict], None] | None = None
    rep_write_every: int = 5  # publish edge on-chain every N calls per provider

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
        if not result.receipt.settled:
            # Couldn't even pay (insufficient balance) -> stop.
            return False

        quality = evaluate(request, result.body, self.settings.eval_model)
        delivered = result.body is not None and result.body.get("data") is not None
        self.bandit.observe(arm.provider_id, quality, result.receipt.amount_usdc, delivered=delivered)
        self._call_count += 1

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
            rc = self.adapter.write_reputation(provider.id, score, calls=calls)
            self._emit("reputation", provider=provider.name,
                       score_bps=score, tx=rc.tx_hash, explorer=rc.explorer_url)

        if self.bandit.arms[provider.id].retired:
            score = self.bandit.quality_bps(provider.id)
            calls = self.bandit.arms[provider.id].calls
            rc = self.adapter.write_reputation(provider.id, score, calls=calls, retire=True)
            self._emit("retire", provider=provider.name,
                       reason="delivery below floor with confidence",
                       tx=rc.tx_hash, explorer=rc.explorer_url)
        return True

    def run(self, request_factory: Callable[[], dict] | None = None) -> dict:
        request_factory = request_factory or (lambda: {"query": "default"})
        self._emit("start", budget=self.adapter.balance_usdc(),
                   providers=len(self.providers))
        while self._call_count < self.settings.max_calls:
            if not self.step(request_factory()):
                break
        return self.summary()

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
                    "calls": a.calls,
                    "spend": round(a.spend_usdc, 4),
                    "mean_quality": round(a.mean_quality, 4),
                    "edge": round(a.mean_edge, 4),
                    "retired": a.retired,
                }
                for a in ranking
            ],
        }
