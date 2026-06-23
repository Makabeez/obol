"""Live Arc adapter -- talks to the sidecar (sidecar/payer.mjs) over localhost.

The sidecar is the only process that holds a key or touches Arc. It exposes:
    GET  /health                          -> { ok, chain, registry }
    GET  /balance                         -> { spent, limit, remaining, gateway }
    POST /pay      {url,method,body}       -> { ok, status, body, amountUsdc, paymentId }
    POST /reputation {providerId,scoreBps,calls,retire} -> { txHash }

Note on proof: Gateway batches payments, so /pay returns a paymentId + amount, NOT an
immediate tx hash. The individual Arc txs come from /reputation (recordScore/retire),
which are the explorer-visible on-chain proof for the demo.

Start the sidecar first:  cd sidecar && node --env-file=.env payer.mjs
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from .adapter import ArcAdapter, FetchResult, PaymentReceipt


class LiveArcAdapter(ArcAdapter):
    def __init__(self, budget_usdc: float = 0.0,
                 sidecar_url: str = "http://127.0.0.1:8401", explorer_base: str = ""):
        self.budget_usdc = float(budget_usdc)
        self.explorer_base = explorer_base.rstrip("/") + "/" if explorer_base else ""
        self.base = (sidecar_url or os.environ.get("OBOL_SIDECAR_URL",
                     "http://127.0.0.1:8401")).rstrip("/")
        self.timeout = float(os.environ.get("OBOL_SIDECAR_TIMEOUT", "60"))
        # Confirm the sidecar is up and seed the local balance from it.
        self._check_health()
        self._balance = self._read_balance()

    # ---- HTTP helpers -----------------------------------------------------
    def _get(self, path: str) -> dict:
        req = urllib.request.Request(self.base + path, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode() or "{}")

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:  # sidecar returns JSON errors with a status
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                return {"ok": False, "error": f"http {e.code}"}

    def _check_health(self) -> None:
        try:
            h = self._get("/health")
        except Exception as e:
            raise RuntimeError(
                f"sidecar not reachable at {self.base} -- start it with "
                f"`cd sidecar && node --env-file=.env payer.mjs`  ({e})"
            )
        if not h.get("ok"):
            raise RuntimeError(f"sidecar unhealthy: {h}")

    def _read_balance(self) -> float:
        sidecar_bal = None
        try:
            b = self._get("/balance")
            for key in ("remaining", "gateway"):
                v = b.get(key)
                if isinstance(v, (int, float)):
                    sidecar_bal = float(v)
                    break
        except Exception:
            sidecar_bal = None
        # Safety: never let a live run spend past the --budget the user set, even if the
        # sidecar's spend limit / Gateway balance is higher.
        if self.budget_usdc > 0 and sidecar_bal is not None:
            return min(sidecar_bal, self.budget_usdc)
        if self.budget_usdc > 0:
            return self.budget_usdc
        return sidecar_bal if sidecar_bal is not None else 0.0

    def balance_usdc(self) -> float:
        return round(self._balance, 6)

    # ---- pay-per-call -----------------------------------------------------
    def pay_and_fetch(self, provider, request: dict) -> FetchResult:
        t0 = time.perf_counter()
        price = float(provider.price_usdc)
        if self._balance < price:
            return FetchResult(provider.id, ok=False, body=None,
                               receipt=PaymentReceipt("", provider.id, 0.0, settled=False),
                               latency_ms=0.0)

        out = self._post("/pay", {
            "url": provider.url,
            "method": request.get("method", "GET"),
            "body": request.get("body"),
        })
        settled = bool(out.get("ok"))
        # Gateway batches: the receipt's "hash" is the paymentId (settlement is async).
        pid = out.get("paymentId") or ""
        amount = float(out.get("amountUsdc") or (price if settled else 0.0))
        if settled:
            self._balance -= amount
        receipt = PaymentReceipt(
            tx_hash=pid,                      # paymentId, not an on-chain tx (Gateway batch)
            provider_id=provider.id,
            amount_usdc=amount,
            settled=settled,
            explorer_url="",                  # batch settlement; no per-call explorer link
        )
        return FetchResult(
            provider.id, ok=settled, body=out.get("body"),
            receipt=receipt, latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # ---- on-chain reputation (REAL Arc tx) --------------------------------
    def write_reputation(self, provider_id: str, score_bps: int, calls: int,
                         retire: bool = False) -> PaymentReceipt:
        out = self._post("/reputation", {
            "providerId": provider_id,
            "scoreBps": int(score_bps),
            "calls": int(calls),
            "retire": bool(retire),
        })
        tx = out.get("txHash") or ""
        return PaymentReceipt(
            tx, provider_id, 0.0, settled=bool(tx),
            explorer_url=(self.explorer_base + tx) if (self.explorer_base and tx) else "",
        )
