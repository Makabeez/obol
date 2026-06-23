"""End-to-end: the agent runs against sim sellers, stays within budget, and ends up
spending most of its money on the genuinely good providers rather than the rug."""

from obol.agent import Agent
from obol.arc.sim import SimArcAdapter
from obol.config import load_providers, load_settings


def test_agent_sim_run_stays_in_budget_and_learns():
    providers = load_providers()
    settings = load_settings({"budget_usdc": 3.0, "max_calls": 600, "min_edge": 0.05})
    adapter = SimArcAdapter(settings.budget_usdc, seed=11)
    agent = Agent(providers, adapter, settings)

    summary = agent.run(request_factory=lambda: {"query": "vol.btc"})

    # Never overspend.
    assert adapter.balance_usdc() >= -1e-9
    assert summary["spend"] <= settings.budget_usdc + 1e-9
    assert summary["calls"] > 0

    by_id = {r["provider"]: r for r in summary["ranking"]}
    # The rug-seller (85% dropout) should be retired or barely funded.
    rug = by_id["rug-seller"]
    gem = by_id["alpha-feed"]
    assert rug["retired"] or rug["calls"] < gem["calls"]
    # The cheap gem should earn more calls than the overpriced trap.
    assert gem["calls"] > by_id["fee-trap"]["calls"]


def test_agent_emits_events():
    providers = load_providers()
    settings = load_settings({"budget_usdc": 0.5, "max_calls": 100})
    adapter = SimArcAdapter(settings.budget_usdc, seed=12)
    events = []
    agent = Agent(providers, adapter, settings, on_event=events.append)
    agent.run()
    kinds = {e["kind"] for e in events}
    assert "start" in kinds and "buy" in kinds
