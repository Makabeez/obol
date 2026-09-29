"""Simulated Arc: a wallet that debits USDC and sellers with hidden quality.

This is what makes the allocation brain demoable today. Each sim seller has a hidden
quality distribution and a dropout rate (takes the money, returns junk). The agent sees
none of that -- only the payload and what it paid -- exactly as it will against real
x402 endpoints. The bandit has to *learn* which sellers are worth it.
"""

from __future__ import annotations

import random
import time

from .adapter import ArcAdapter, FetchResult, PaymentReceipt


class SimArcAdapter(ArcAdapter):
    def __init__(self, budget_usdc: float, seed: int | None = None):
        self._balance = float(budget_usdc)
        self._rng = random.Random(seed)
        self._nonce = 0
        self._rep_writes: list[PaymentReceipt] = []

    def balance_usdc(self) -> float:
        return round(self._balance, 6)

    def _tx(self) -> str:
        self._nonce += 1
        return "0xsim" + format(self._rng.getrandbits(128), "032x")[:60]

    def pay_and_fetch(self, provider, request: dict) -> FetchResult:
        t0 = time.perf_counter()
        price = float(provider.price_usdc)

        if self._balance < price:
            receipt = PaymentReceipt(self._tx(), provider.id, 0.0, settled=False)
            return FetchResult(provider.id, ok=False, body=None, receipt=receipt, latency_ms=0.0)

        # Settle the x402 challenge.
        self._balance -= price
        receipt = PaymentReceipt(
            tx_hash=self._tx(),
            provider_id=provider.id,
            amount_usdc=price,
            settled=True,
            explorer_url="",  # sim tx is not real -> no explorer link
        )

        # Seller behaviour (hidden from the agent).
        dropped = self._rng.random() < float(provider._sim_dropout)
        if dropped:
            body = {"status": "ok", "data": None}  # took payment, delivered nothing useful
        else:
            q = max(0.0, min(1.0, self._rng.gauss(provider._sim_quality_mean, provider._sim_quality_spread)))
            body = {
                "status": "ok",
                "query": request.get("query"),
                "data": {"score": round(q, 4), "fields": int(2 + q * 8)},
            }

        latency = self._rng.uniform(40, 260)
        return FetchResult(provider.id, ok=True, body=body, receipt=receipt, latency_ms=latency)

    def write_reputation(self, provider_id: str, score_bps: int, calls: int = 0,
                         retire: bool = False, evidence: str = "") -> PaymentReceipt:
        r = PaymentReceipt(self._tx(), provider_id, 0.0, settled=True,
                           explorer_url="")  # sim tx is not real -> no explorer link
        self._rep_writes.append(r)
        return r
