"""The Arc adapter: the single seam between Obol and the chain.

Everything chain-touching goes through ArcAdapter. SimArcAdapter runs anywhere with no
creds and lets the allocation brain be developed and demoed today. LiveArcAdapter wires
the same interface to a real Circle agent wallet + x402 settlement on Arc, on your machine.

Swapping sim -> live is one flag. No call site changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PaymentReceipt:
    """Proof that a per-call payment settled on Arc."""

    tx_hash: str
    provider_id: str
    amount_usdc: float
    settled: bool
    explorer_url: str = ""


@dataclass
class FetchResult:
    """What the seller returned after payment."""

    provider_id: str
    ok: bool            # transport-level success (got a 200 after paying)
    body: dict | None   # the payload the seller delivered
    receipt: PaymentReceipt
    latency_ms: float


class ArcAdapter(ABC):
    """Holds the agent wallet and executes x402 pay-per-call against a seller."""

    @abstractmethod
    def balance_usdc(self) -> float:
        ...

    @abstractmethod
    def pay_and_fetch(self, provider, request: dict) -> FetchResult:
        """Hit a provider's x402 endpoint, settle the 402 challenge in USDC, return payload."""
        ...

    @abstractmethod
    def write_reputation(self, provider_id: str, score_bps: int, calls: int = 0,
                         retire: bool = False) -> PaymentReceipt:
        """Record a provider's delivered-quality score (and retirement) on-chain."""
        ...
