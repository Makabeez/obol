"""ValueBandit -- budgeted allocation across paid providers.

The problem, in trading terms: N providers, each call costs USDC and returns realized
value in [0,1]. Fixed budget. Maximize total realized value. That is position sizing
under uncertainty -- put capital where the edge is, cut the losers, keep probing the
unknowns in case they're cheap alpha.

Mechanism:
  * Each provider's per-call quality q in [0,1] is modelled Beta(alpha, beta).
  * Its *edge* is expected value-per-USDC = E[q] / price.
  * Selection is Thompson sampling on edge: sample q~Beta for each live arm, divide by
    price, buy from the argmax. Cheap-but-unproven arms get explored; proven-cheap arms
    get exploited; expensive-mediocre arms starve themselves.
  * Cut-off: once an arm has been probed enough, if the upper confidence bound on its
    edge is still below `min_edge`, retire it -- we're confident it's not worth buying.

No LLM in the loop here. The smartness is the allocation; evaluation feeds it a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import beta as beta_dist


@dataclass
class Arm:
    provider_id: str
    price_usdc: float
    alpha: float = 1.0          # Beta prior (uniform) -- quality, decays with time
    beta: float = 1.0
    calls: int = 0
    spend_usdc: float = 0.0
    realized_value: float = 0.0  # sum of quality observed
    retired: bool = False
    # Delivery is tracked separately and NEVER decays: a provider that takes payment and
    # returns nothing is a scammer, and that verdict must be sticky. Quality (above) can
    # drift; delivery is a hard fact.
    delivered: int = 0
    seen: int = 0

    @property
    def mean_quality(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def delivery_rate(self) -> float:
        return self.delivered / self.seen if self.seen else 1.0

    @property
    def mean_edge(self) -> float:
        return self.mean_quality / self.price_usdc if self.price_usdc > 0 else 0.0

    def quality_ucb(self, confidence: float) -> float:
        """Upper confidence bound on quality, via the Beta quantile."""
        return float(beta_dist.ppf(confidence, self.alpha, self.beta))

    def delivery_ucb(self, confidence: float) -> float:
        """Optimistic (upper) bound on delivery rate, from non-decaying counts."""
        return float(beta_dist.ppf(confidence, self.delivered + 1, (self.seen - self.delivered) + 1))

    def edge_ucb(self, confidence: float) -> float:
        """Upper confidence bound on edge, via the Beta quantile on quality."""
        return self.quality_ucb(confidence) / self.price_usdc if self.price_usdc > 0 else 0.0

    def update(self, quality: float, paid: float, delivered: bool) -> None:
        quality = max(0.0, min(1.0, quality))
        # Continuous Beta update: treat quality as a fractional success.
        self.alpha += quality
        self.beta += (1.0 - quality)
        self.calls += 1
        self.spend_usdc += paid
        self.realized_value += quality
        self.seen += 1
        if delivered:
            self.delivered += 1


class ValueBandit:
    def __init__(
        self,
        providers,
        min_edge: float,
        min_delivery: float = 0.50,
        cutoff_confidence: float = 0.90,
        explore_rounds: int = 1,
        decay: float = 1.0,
        epsilon: float = 0.0,
        seed: int | None = None,
    ):
        self.arms: dict[str, Arm] = {
            p.id: Arm(provider_id=p.id, price_usdc=float(p.price_usdc)) for p in providers
        }
        self.min_edge = float(min_edge)
        # A provider must deliver (return real data) at least this fraction of the time,
        # or it is retired as a scammer. Non-decaying, so the verdict sticks.
        self.min_delivery = float(min_delivery)
        self.cutoff_confidence = float(cutoff_confidence)
        self.explore_rounds = int(explore_rounds)
        # decay < 1.0 ages each arm's evidence toward the prior as it's used, bounding
        # confidence so the agent keeps re-evaluating instead of locking onto one winner
        # forever -- and so it tracks providers whose quality drifts over time.
        self.decay = float(decay)
        # epsilon forces an occasional re-probe of the least-recently-checked live arm,
        # so a provider that was mediocre early still gets re-examined later.
        self.epsilon = float(epsilon)
        self._rng = np.random.default_rng(seed)

    def live_arms(self) -> list[Arm]:
        return [a for a in self.arms.values() if not a.retired]

    def select(self, budget_left: float) -> Arm | None:
        """Pick the next provider to buy from, or None when there's nothing worth buying."""
        affordable = [a for a in self.live_arms() if a.price_usdc <= budget_left + 1e-9]
        if not affordable:
            return None

        # Guarantee each arm a minimum number of probes before it can be judged.
        unprobed = [a for a in affordable if a.calls < self.explore_rounds]
        if unprobed:
            return min(unprobed, key=lambda a: a.price_usdc)  # cheapest probe first

        # Epsilon re-exploration: occasionally re-check the least-probed live arm, so the
        # market is continuously re-evaluated rather than judged once and abandoned.
        if self.epsilon > 0 and self._rng.random() < self.epsilon:
            return min(affordable, key=lambda a: a.calls)

        # Thompson sampling on edge.
        best, best_sample = None, -1.0
        for a in affordable:
            q = float(self._rng.beta(a.alpha, a.beta))
            sample_edge = q / a.price_usdc if a.price_usdc > 0 else 0.0
            if sample_edge > best_sample:
                best, best_sample = a, sample_edge
        return best

    def _decay_arm(self, arm: Arm) -> None:
        if self.decay < 1.0:
            arm.alpha = 1.0 + (arm.alpha - 1.0) * self.decay
            arm.beta = 1.0 + (arm.beta - 1.0) * self.decay

    def observe(self, provider_id: str, quality: float, paid: float, delivered: bool = True) -> None:
        arm = self.arms[provider_id]
        self._decay_arm(arm)
        arm.update(quality, paid, delivered)
        if arm.seen < self.explore_rounds:
            return
        # Retirement is for providers that fail to DELIVER -- scammers that take payment
        # and return nothing. This uses non-decaying delivery counts, so the verdict is
        # sticky: a confirmed rug stays cut even as the adaptive layer keeps re-evaluating
        # everyone else. Merely-mediocre-but-delivering providers are NOT retired here;
        # the allocator simply starves them (they keep ~zero share but stay in the pool).
        delivery_fail = arm.delivery_ucb(self.cutoff_confidence) < self.min_delivery
        # Edge gate stays as a guard against a provider that delivers but is hopelessly
        # overpriced relative to the value it returns.
        edge_fail = arm.edge_ucb(self.cutoff_confidence) < self.min_edge
        if delivery_fail or edge_fail:
            arm.retired = True

    def quality_bps(self, provider_id: str) -> int:
        """Running delivered quality as basis points (0-10000), for the on-chain score."""
        return max(0, min(10_000, int(round(self.arms[provider_id].mean_quality * 10_000))))

    def edge_bps(self, provider_id: str) -> int:
        """Running edge as basis points, for the on-chain reputation write."""
        return int(round(self.arms[provider_id].mean_edge * 10_000))

    def ranking(self) -> list[Arm]:
        return sorted(self.arms.values(), key=lambda a: a.mean_edge, reverse=True)
