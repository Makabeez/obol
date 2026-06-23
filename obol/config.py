"""Configuration: agent settings and the provider registry.

Providers are loaded from config/providers.yaml. Each provider is an x402-protected
endpoint the agent can discover and pay. In sim mode the registry also carries the
hidden "ground truth" used by the simulator -- the agent never reads those fields;
they exist only so the simulator can score calls and we can check the bandit learned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Provider:
    """A payable service the agent can buy from."""

    id: str
    name: str
    url: str
    # Price the seller advertises in its 402 challenge, in USDC.
    price_usdc: float
    # Free-text the agent uses to decide whether a result matched intent.
    kind: str = "data"
    tags: list[str] = field(default_factory=list)
    # --- sim-only ground truth (agent never reads these) ---
    _sim_quality_mean: float = 0.5
    _sim_quality_spread: float = 0.15
    _sim_dropout: float = 0.0  # probability the seller takes payment and returns junk

    @property
    def is_sim_truth(self) -> bool:
        return self.url.startswith("sim://")


@dataclass
class Settings:
    mode: str = "sim"  # "sim" | "live"
    budget_usdc: float = 5.0
    # The agent stops buying from a provider once we are confident its value-per-USDC
    # falls below this floor. 0.0 == "worse than just keeping the USDC".
    min_edge: float = 0.05
    # Hard cap on calls so a runaway loop can never drain the wallet.
    max_calls: int = 400
    # Confidence used for the cut-off upper bound (e.g. 0.90 == fairly patient).
    cutoff_confidence: float = 0.90
    explore_rounds: int = 1  # guaranteed probes per provider before any cut-off
    # Keep the agent adaptive: age evidence and occasionally re-probe, so it tracks a
    # live market instead of locking onto the first winner. Set decay=1.0/epsilon=0.0
    # to disable.
    decay: float = 0.997
    epsilon: float = 0.08

    # On-chain
    arc_rpc: str = os.environ.get("ARC_RPC", "")
    reputation_contract: str = os.environ.get("REPUTATION_CONTRACT", "")
    explorer_base: str = os.environ.get("ARC_EXPLORER", "https://explorer.arc.network/tx/")

    # Eval LLM. Wallet-adjacent decision -> route through Anthropic only, never a
    # third-party provider. Leave empty to use the programmatic scorer alone.
    eval_model: str = os.environ.get("OBOL_EVAL_MODEL", "")  # e.g. "claude-opus-4-8"


def load_providers(path: str | Path | None = None) -> list[Provider]:
    path = Path(path) if path else ROOT / "config" / "providers.yaml"
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    out: list[Provider] = []
    for p in raw.get("providers", []):
        out.append(Provider(**p))
    return out


def load_settings(overrides: dict[str, Any] | None = None) -> Settings:
    s = Settings()
    for k, v in (overrides or {}).items():
        if v is not None and hasattr(s, k):
            setattr(s, k, v)
    return s
