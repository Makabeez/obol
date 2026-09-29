"""Live-path logic without a network: a fake adapter that behaves like the sidecar does
against the real Arc mainnet sellers (shapes taken from live 402/500 responses)."""

from obol.agent import Agent
from obol.arc.adapter import ArcAdapter, FetchResult, PaymentReceipt
from obol.config import Provider, load_settings
from obol.eval import evaluator


class FakeSidecar(ArcAdapter):
    def __init__(self, budget):
        self.bal = budget
        self.writes = []

    def balance_usdc(self):
        return self.bal

    def pay_and_fetch(self, p, request):
        if p.id == "good":
            self.bal -= p.price_usdc
            body = {"status": "ok", "data": {"results": [{"url": "https://a"}], "requestId": "x"}}
            return FetchResult(p.id, True, body, PaymentReceipt("0x" + "ab" * 32, p.id, p.price_usdc, True),
                               300.0, evidence="0x" + "ab" * 32, http_status=200)
        if p.id == "always-500":  # seller fails, does not charge
            return FetchResult(p.id, False, {"status": "error", "data": None},
                               PaymentReceipt("no-payment:http-500", p.id, 0.0, False), 900.0,
                               evidence="no-payment:http-500", http_status=500)
        # unfunded gateway rail -> 402 after trying: our problem, not the seller's
        return FetchResult(p.id, False, {"status": "error", "data": None},
                           PaymentReceipt("", p.id, 0.0, False), 500.0, refused=True,
                           evidence="no-payment:http-402", http_status=402)

    def write_reputation(self, provider_id, score_bps, calls=0, retire=False, evidence=""):
        self.writes.append((provider_id, score_bps, calls, retire, evidence))
        return PaymentReceipt("0x" + "cd" * 32, provider_id, 0.0, True)


def _prov(pid, price, expect=None):
    return Provider(id=pid, name=pid, url=f"https://example.test/{pid}", price_usdc=price,
                    expect=expect or [])


def test_live_run_retires_failing_seller_skips_unpayable_and_flushes(monkeypatch):
    monkeypatch.setattr(evaluator, "_arc_head", lambda: None)
    provs = [_prov("good", 0.007, ["results"]), _prov("always-500", 0.001), _prov("unfunded", 0.0005)]
    ad = FakeSidecar(0.05)
    agent = Agent(provs, ad, load_settings({"budget_usdc": 0.05, "max_calls": 40}))
    summary = agent.run()

    by = {r["provider"]: r for r in summary["ranking"]}
    assert by["always-500"]["retired"] and by["always-500"]["spend"] == 0
    assert by["good"]["calls"] >= 3 and by["good"]["spend"] <= 0.05 + 1e-9
    retire_writes = [w for w in ad.writes if w[0] == "always-500" and w[3]]
    assert retire_writes and retire_writes[0][4] == "no-payment:http-500"
    good_writes = [w for w in ad.writes if w[0] == "good"]
    assert good_writes and good_writes[-1][4].startswith("0x") and good_writes[-1][1] >= 7000
    assert good_writes[-1][2] == by["good"]["calls"]  # final flush reflects every call
    assert not [w for w in ad.writes if w[0] == "unfunded"]  # never observed -> never scored


def test_live_score_freshness(monkeypatch):
    p = _prov("cra", 0.001)
    monkeypatch.setattr(evaluator, "_arc_head", lambda: 21_600_000)
    fresh = {"status": "ok", "data": {"current": {"blockNumber": 21_599_990}, "x": 1}}
    stale = {"status": "ok", "data": {"current": {"blockNumber": 21_564_000}, "x": 1}}  # ~5 h behind
    assert evaluator.live_score(p, fresh, 400) > 0.9
    assert evaluator.live_score(p, stale, 400) < 0.5
    assert evaluator.live_score(p, {"status": "error", "data": None}, 100) == 0.0
