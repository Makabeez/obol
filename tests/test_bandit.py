"""The allocator is the differentiator, so it gets the real tests:
it must (1) converge spend onto the best edge and (2) retire genuine duds."""

from obol.allocator import ValueBandit
from obol.config import Provider


def _providers():
    return [
        Provider(id="cheap-good", name="cheap-good", url="sim://a", price_usdc=0.002,
                 _sim_quality_mean=0.85),
        Provider(id="pricey-good", name="pricey-good", url="sim://b", price_usdc=0.010,
                 _sim_quality_mean=0.95),
        Provider(id="dud", name="dud", url="sim://c", price_usdc=0.020,
                 _sim_quality_mean=0.20),
    ]


def test_converges_to_best_edge():
    import numpy as np
    rng = np.random.default_rng(0)
    truth = {"cheap-good": (0.85, 0.002), "pricey-good": (0.95, 0.010), "dud": (0.20, 0.020)}
    b = ValueBandit(_providers(), min_edge=0.05, explore_rounds=2, seed=1)

    for _ in range(2000):
        arm = b.select(budget_left=10_000)
        assert arm is not None
        q_mean, price = truth[arm.provider_id]
        q = float(np.clip(rng.normal(q_mean, 0.05), 0, 1))
        b.observe(arm.provider_id, q, price)

    ranking = b.ranking()
    # cheap-good has the highest value-per-USDC (0.85/0.002 >> 0.95/0.010).
    assert ranking[0].provider_id == "cheap-good"
    # the agent should have spent the most calls on it.
    assert b.arms["cheap-good"].calls > b.arms["pricey-good"].calls
    assert b.arms["cheap-good"].calls > b.arms["dud"].calls


def test_delivery_gate_retires_a_rug():
    # A provider that takes payment but returns nothing (delivered=False) must be cut --
    # and the verdict is sticky. Quality value is deliberately decent here to prove it's
    # the DELIVERY gate doing the work, not quality.
    b = ValueBandit(_providers(), min_edge=0.0, min_delivery=0.5,
                    explore_rounds=2, cutoff_confidence=0.90, seed=3)
    for _ in range(20):
        b.observe("dud", 0.6, 0.020, delivered=False)
    assert b.arms["dud"].retired


def test_mediocre_but_delivering_is_not_retired():
    # Low value but it does deliver -> NOT a scammer. The allocator should starve it, but
    # it stays in the pool rather than being banned.
    b = ValueBandit(_providers(), min_edge=0.0, min_delivery=0.5,
                    explore_rounds=2, cutoff_confidence=0.90, seed=3)
    for _ in range(40):
        b.observe("dud", 0.2, 0.020, delivered=True)
    assert not b.arms["dud"].retired


def test_retires_when_edge_truly_low():
    # A provider that is expensive AND bad: edge well below the floor.
    ps = [Provider(id="expensive-bad", name="x", url="sim://x", price_usdc=1.0)]
    b = ValueBandit(ps, min_edge=0.5, explore_rounds=2, cutoff_confidence=0.90, seed=4)
    for _ in range(40):
        b.observe("expensive-bad", 0.05, 1.0)  # edge ~ 0.05, floor 0.5
    assert b.arms["expensive-bad"].retired


def test_select_returns_none_when_unaffordable():
    ps = [Provider(id="pricey", name="p", url="sim://p", price_usdc=5.0)]
    b = ValueBandit(ps, min_edge=0.05, seed=5)
    assert b.select(budget_left=1.0) is None


def test_epsilon_reexploration_spreads_probes():
    # With epsilon>0 the agent must keep re-probing non-top providers, not lock onto one.
    import numpy as np
    rng = np.random.default_rng(7)
    ps = _providers()
    truth = {"cheap-good": 0.85, "pricey-good": 0.95, "dud": 0.20}
    b = ValueBandit(ps, min_edge=0.01, min_delivery=0.0, explore_rounds=2,
                    decay=0.997, epsilon=0.15, seed=7)
    for _ in range(800):
        arm = b.select(budget_left=10_000)
        q = float(np.clip(rng.normal(truth[arm.provider_id], 0.05), 0, 1))
        b.observe(arm.provider_id, q, arm.price_usdc)
    # cheap-good still wins the most calls...
    assert b.arms["cheap-good"].calls == max(a.calls for a in b.arms.values())
    # ...but the others keep getting re-checked (not abandoned at the explore floor).
    assert b.arms["pricey-good"].calls > 5


def test_decay_bounds_confidence():
    # Decay should stop a single arm's evidence from growing without bound.
    b = ValueBandit([Provider(id="x", name="x", url="sim://x", price_usdc=0.002)],
                    min_edge=0.01, min_delivery=0.0, decay=0.99, seed=1)
    for _ in range(5000):
        b.observe("x", 0.9, 0.002)
    arm = b.arms["x"]
    # Without decay alpha+beta would be ~5000; decay caps it far lower.
    assert (arm.alpha + arm.beta) < 500
