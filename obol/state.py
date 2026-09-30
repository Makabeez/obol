"""Cross-run memory for the Obol agent.

Why this exists: every run used to start the bandit from a blank prior and then write that
run's numbers over the on-chain record, so a seller's published history was replaced by
whatever the latest 15-call run saw. Reputation has to accumulate.

Two sources, reconciled at startup:
  * runs/state.json (local, exact): per-provider Beta(alpha, beta), calls, delivery counts,
    spend, last evidence. Written at the end of every live run.
  * the ReputationRegistry on Arc (public): scoreBps, calls, retired. The agent reads its own
    record before paying, exactly as any other agent would.

If the chain is ahead of the local file (a run crashed after writing on-chain but before
saving state), the chain wins for score and calls. Delivery counts are not on-chain, so they
come from the local file only.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_STATE = Path(os.environ.get("OBOL_STATE", "runs/state.json"))


@dataclass
class ArmState:
    alpha: float = 1.0
    beta: float = 1.0
    calls: int = 0
    seen: int = 0
    delivered: int = 0
    spend_usdc: float = 0.0
    evidence: str = ""
    retired: bool = False

    @property
    def score_bps(self) -> int:
        return max(0, min(10_000, int(round(self.alpha / (self.alpha + self.beta) * 10_000))))


def load_state(path: Path = DEFAULT_STATE) -> dict[str, ArmState]:
    try:
        raw = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for pid, v in (raw.get("providers") or {}).items():
        out[pid] = ArmState(**{k: v[k] for k in ArmState.__dataclass_fields__ if k in v})
    return out


def save_state(state: dict[str, ArmState], path: Path = DEFAULT_STATE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "providers": {k: asdict(v) for k, v in state.items()}}, indent=2))
    tmp.replace(path)


def from_chain(record: dict) -> ArmState:
    """Invert the published score: score = alpha / (alpha + beta) with alpha + beta = calls + 2
    (uniform Beta(1,1) prior plus one fractional observation per call)."""
    n = int(record.get("calls") or 0)
    q = float(record.get("scoreBps") or 0) / 10_000
    total = n + 2.0
    return ArmState(alpha=q * total, beta=(1.0 - q) * total, calls=n,
                    retired=bool(record.get("retired")))


def reconcile(local: ArmState | None, chain: dict | None) -> ArmState | None:
    """Pick the starting point for one provider."""
    if chain is None and local is None:
        return None
    if chain is None or int(chain.get("calls") or 0) == 0:
        return local
    c = from_chain(chain)
    if local is None:
        return c  # no delivery history known: delivery gate starts fresh
    if c.calls > local.calls:
        # chain is ahead (crash between the on-chain write and save_state): trust the chain
        # for score/calls, keep what we know locally about delivery.
        c.seen, c.delivered = local.seen, local.delivered
        c.spend_usdc, c.evidence = local.spend_usdc, local.evidence
        local = c
    local.retired = local.retired or bool(chain.get("retired"))
    return local


def from_run_files(paths: list[str | Path]) -> dict[str, ArmState]:
    """Rebuild cumulative per-provider state from saved run files (runs/*.json).

    Uses each run's per-provider aggregates (complete; the feed is capped at 25 entries):
    alpha += sum of delivered quality, beta += calls - that sum. Undelivered calls score 0.
    Last evidence per provider comes from the newest run's feed that mentions it."""
    state: dict[str, ArmState] = {}
    for p in paths:
        led = json.loads(Path(p).read_text())["ledger"]
        for pid, a in led["providers"].items():
            s = state.setdefault(pid, ArmState())
            calls, value, drops = int(a["calls"]), float(a["value"]), int(a.get("drops", 0))
            s.alpha += value
            s.beta += calls - value
            s.calls += calls
            s.seen += calls
            s.delivered += calls - drops
            s.spend_usdc = round(s.spend_usdc + float(a["spend"]), 6)
        for e in reversed(led.get("feed", [])):  # feed is newest-first; walk oldest -> newest
            if e.get("tx_hash") and e["provider_id"] in state:
                state[e["provider_id"]].evidence = e["tx_hash"]
    return state
