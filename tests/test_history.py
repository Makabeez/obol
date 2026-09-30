"""Reputation must accumulate across runs, never be overwritten by the latest short run."""

import json

from obol.agent import Agent
from obol.arc.adapter import ArcAdapter, FetchResult, PaymentReceipt
from obol.config import Provider, load_settings
from obol.eval import evaluator
from obol.state import ArmState, from_chain, from_run_files, load_state, reconcile


class ChainSidecar(ArcAdapter):
    """Fake sidecar backed by an in-memory 'registry' that persists across agent runs."""

    def __init__(self, budget, registry):
        self.bal, self.reg, self.writes = budget, registry, []

    def balance_usdc(self):
        return self.bal

    def pay_and_fetch(self, p, request):
        if p.id == "good":
            self.bal -= p.price_usdc
            body = {"status": "ok", "data": {"results": [1], "requestId": "x"}}
            return FetchResult(p.id, True, body, PaymentReceipt("0x" + "ab" * 32, p.id, p.price_usdc, True),
                               300.0, evidence="0x" + "ab" * 32, http_status=200)
        return FetchResult(p.id, False, {"status": "error", "data": None},
                           PaymentReceipt("no-payment:http-500", p.id, 0.0, False), 900.0,
                           evidence="no-payment:http-500", http_status=500)

    def read_reputation(self, pid):
        return dict(self.reg[pid]) if pid in self.reg else None

    def write_reputation(self, pid, score_bps, calls=0, retire=False, evidence=""):
        assert not self.reg.get(pid, {}).get("retired"), "contract reverts on a retired provider"
        self.reg[pid] = {"scoreBps": score_bps, "calls": calls, "retired": retire}
        self.writes.append((pid, score_bps, calls, retire, evidence))
        return PaymentReceipt("0x" + "cd" * 32, pid, 0.0, True)


def _prov(pid, price, expect=None):
    return Provider(id=pid, name=pid, url=f"https://example.test/{pid}", price_usdc=price, expect=expect or [])


def test_chain_inversion_roundtrip():
    st = ArmState(alpha=1 + 12.75, beta=1 + 15 - 12.75, calls=15)
    back = from_chain({"scoreBps": st.score_bps, "calls": 15})
    assert abs(back.alpha / (back.alpha + back.beta) - st.alpha / (st.alpha + st.beta)) < 1e-4
    assert back.calls == 15 and abs(back.alpha + back.beta - 17) < 1e-9


def test_reconcile_prefers_chain_when_ahead_but_keeps_delivery():
    local = ArmState(alpha=5, beta=2, calls=5, seen=5, delivered=4, evidence="gateway:x")
    st = reconcile(local, {"scoreBps": 8000, "calls": 9, "retired": False})
    assert st.calls == 9 and st.seen == 5 and st.delivered == 4 and st.evidence == "gateway:x"
    assert reconcile(local, {"scoreBps": 1, "calls": 3}).calls == 5  # local ahead: keep local


def test_second_run_accumulates_instead_of_overwriting(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluator, "_arc_head", lambda: None)
    reg, state = {}, tmp_path / "state.json"
    provs = [_prov("good", 0.007, ["results"])]
    s = load_settings({"budget_usdc": 0.05, "max_calls": 4, "epsilon": 0.0})

    Agent(provs, ChainSidecar(0.05, reg), s, state_path=state).run()
    first = reg["good"]["calls"]
    Agent(provs, ChainSidecar(0.05, reg), s, state_path=state).run()

    assert first == 4 and reg["good"]["calls"] == 8
    assert load_state(state)["good"].calls == 8


def test_failures_across_runs_retire_the_seller(monkeypatch, tmp_path):
    """Two failures in run 1 are not enough to be confident; the third, in run 2, is."""
    monkeypatch.setattr(evaluator, "_arc_head", lambda: None)
    reg, state = {}, tmp_path / "state.json"
    provs = [_prov("good", 0.007, ["results"]), _prov("bad", 0.001)]
    st = ArmState(alpha=1.0, beta=3.0, calls=2, seen=2, delivered=0, evidence="no-payment:http-500")
    json.dump({"providers": {"bad": st.__dict__}}, open(state, "w"))
    reg["bad"] = {"scoreBps": st.score_bps, "calls": 2, "retired": False}

    ad = ChainSidecar(0.05, reg)
    agent = Agent(provs, ad, load_settings({"budget_usdc": 0.05, "max_calls": 3, "epsilon": 0.0}),
                  state_path=state)
    agent.run()
    # 2 failures carried in; the first new probe makes 3 -> retired, evidence attached, persisted
    assert reg["bad"]["retired"]
    retire = [w for w in ad.writes if w[0] == "bad" and w[3]]
    assert retire and retire[0][4] == "no-payment:http-500" and retire[0][2] >= 2
    assert load_state(state)["bad"].retired


def test_retired_on_chain_is_never_written_again(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluator, "_arc_head", lambda: None)
    reg = {"bad": {"scoreBps": 2000, "calls": 3, "retired": True}}
    ad = ChainSidecar(0.05, reg)
    Agent([_prov("good", 0.007, ["results"]), _prov("bad", 0.001)], ad,
          load_settings({"budget_usdc": 0.05, "max_calls": 5}), state_path=tmp_path / "s.json").run()
    assert not [w for w in ad.writes if w[0] == "bad"]


def test_rebuild_from_run_files(tmp_path):
    def run(providers, feed):
        return {"ledger": {"providers": providers, "feed": feed}}
    r1 = tmp_path / "r1.json"; r2 = tmp_path / "r2.json"
    json.dump(run({"x": {"calls": 3, "value": 2.4, "spend": 0.021, "drops": 0},
                   "f": {"calls": 2, "value": 0.0, "spend": 0.0, "drops": 2}},
                  [{"provider_id": "x", "tx_hash": "0xold"}]), open(r1, "w"))
    json.dump(run({"x": {"calls": 1, "value": 0.6, "spend": 0.007, "drops": 0},
                   "f": {"calls": 1, "value": 0.0, "spend": 0.0, "drops": 1}},
                  [{"provider_id": "f", "tx_hash": "no-payment:http-500"},
                   {"provider_id": "x", "tx_hash": "0xnew"}]), open(r2, "w"))
    st = from_run_files([r1, r2])
    assert st["x"].calls == 4 and st["x"].evidence == "0xnew" and st["x"].delivered == 4
    assert st["f"].calls == 3 and st["f"].delivered == 0 and st["f"].score_bps == 2000
