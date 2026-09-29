"""Live Arc adapter -- talks to the sidecar (sidecar/payer.mjs) over localhost.

The sidecar is the only process that holds a key or touches Arc mainnet, and it enforces
every payment guardrail (allowlist by provider id, payTo pin, Arc-only, per-call and
lifetime caps). This adapter only asks it to pay a provider *by id*.

    GET  /health    GET /balance
    POST /pay        {providerId}  -> {ok, paid, httpStatus, amountUsdc, scheme, evidence, latencyMs, body}
    POST /reputation {providerId, scoreBps, calls, retire, evidence} -> {txHash}

Start the sidecar first:  cd sidecar && node --env-file=.env payer.mjs
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .adapter import ArcAdapter, FetchResult, PaymentReceipt


class LiveArcAdapter(ArcAdapter):
    def __init__(self, budget_usdc: float = 0.0,
                 sidecar_url: str = "http://127.0.0.1:8401", explorer_base: str = ""):
        self.budget_usdc = float(budget_usdc)
        self.explorer_base = explorer_base.rstrip("/") + "/" if explorer_base else ""
        self.base = (sidecar_url or os.environ.get("OBOL_SIDECAR_URL",
                     "http://127.0.0.1:8401")).rstrip("/")
        self.timeout = float(os.environ.get("OBOL_SIDECAR_TIMEOUT", "90"))
        self._check_health()
        self._balance = self._read_balance()

    # ---- HTTP helpers -----------------------------------------------------
    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            return json.loads(r.read().decode() or "{}")

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                return {"ok": False, "error": f"http {e.code}"}

    def _check_health(self) -> None:
        try:
            h = self._get("/health")
        except Exception as e:
            raise RuntimeError(f"sidecar not reachable at {self.base} -- start it with "
                               f"`cd sidecar && node --env-file=.env payer.mjs` ({e})")
        if not h.get("ok") or h.get("chainId") != 5042:
            raise RuntimeError(f"sidecar unhealthy or not on Arc mainnet: {h}")

    def _read_balance(self) -> float:
        try:
            remaining = float(self._get("/balance").get("remaining"))
        except Exception:
            remaining = None
        # Never spend past --budget, even if the sidecar's lifetime cap is higher.
        if remaining is None:
            return self.budget_usdc
        return min(remaining, self.budget_usdc) if self.budget_usdc > 0 else remaining

    def balance_usdc(self) -> float:
        return round(self._balance, 6)

    # ---- pay-per-call -----------------------------------------------------
    def pay_and_fetch(self, provider, request: dict) -> FetchResult:
        empty = PaymentReceipt("", provider.id, 0.0, settled=False)
        if self._balance < float(provider.price_usdc):
            return FetchResult(provider.id, False, None, empty, 0.0, refused=True,
                               evidence="refused:budget")
        out = self._post("/pay", {"providerId": provider.id})
        paid = bool(out.get("paid"))
        amount = float(out.get("amountUsdc") or 0.0)
        status = int(out.get("httpStatus") or 0)
        evidence = str(out.get("evidence") or "")
        if paid:
            self._balance -= amount
        # We could not pay: policy/cap refusal (status 0) or the seller still wants
        # payment after we tried (402 = our funds/rail problem, not the seller's fault).
        refused = (not paid) and (status in (0, 402))
        tx = evidence if evidence.startswith("0x") else ""
        receipt = PaymentReceipt(
            tx_hash=tx or evidence, provider_id=provider.id, amount_usdc=amount, settled=paid,
            explorer_url=(self.explorer_base + tx) if (self.explorer_base and tx) else "",
        )
        return FetchResult(provider.id, ok=bool(out.get("ok")), body=out.get("body"),
                           receipt=receipt, latency_ms=float(out.get("latencyMs") or 0.0),
                           refused=refused, evidence=evidence, http_status=status)

    # ---- on-chain reputation (REAL Arc tx) --------------------------------
    def write_reputation(self, provider_id: str, score_bps: int, calls: int = 0,
                         retire: bool = False, evidence: str = "") -> PaymentReceipt:
        out = self._post("/reputation", {
            "providerId": provider_id, "scoreBps": int(score_bps), "calls": int(calls),
            "retire": bool(retire), "evidence": evidence,
        })
        tx = out.get("txHash") or ""
        if not tx:
            print(f"  !  reputation write failed for {provider_id}: {out.get('error')}")
        return PaymentReceipt(tx, provider_id, 0.0, settled=bool(tx),
                              explorer_url=(self.explorer_base + tx) if (self.explorer_base and tx) else "")
