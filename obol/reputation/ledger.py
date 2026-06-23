"""Reputation ledger -- the record of who delivered.

Every settled call appends an entry locally (for the dashboard and the demo), and the
agent periodically writes each provider's running edge to ReputationRegistry.sol on Arc.
That on-chain edge is the public artifact: any other agent can read it before buying.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class LedgerEntry:
    ts: float
    provider_id: str
    provider_name: str
    paid_usdc: float
    quality: float
    edge: float
    tx_hash: str
    explorer_url: str
    delivered: bool


@dataclass
class Ledger:
    path: Path | None = None
    entries: list[LedgerEntry] = field(default_factory=list)

    def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)
        if self.path:
            with open(self.path, "a") as fh:
                fh.write(json.dumps(asdict(entry)) + "\n")

    def total_spend(self) -> float:
        return round(sum(e.paid_usdc for e in self.entries), 6)

    def total_value(self) -> float:
        return round(sum(e.quality for e in self.entries if e.delivered), 4)

    def by_provider(self) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for e in self.entries:
            a = agg.setdefault(e.provider_id, {
                "name": e.provider_name, "calls": 0, "spend": 0.0,
                "value": 0.0, "drops": 0,
            })
            a["calls"] += 1
            a["spend"] += e.paid_usdc
            a["value"] += e.quality if e.delivered else 0.0
            a["drops"] += 0 if e.delivered else 1
        for a in agg.values():
            a["spend"] = round(a["spend"], 6)
            a["value"] = round(a["value"], 4)
            a["edge"] = round(a["value"] / a["spend"], 4) if a["spend"] > 0 else 0.0
        return agg

    def snapshot(self) -> dict:
        return {
            "spend": self.total_spend(),
            "value": self.total_value(),
            "calls": len(self.entries),
            "providers": self.by_provider(),
            "feed": [asdict(e) for e in self.entries[-25:][::-1]],
            "updated": time.time(),
        }
