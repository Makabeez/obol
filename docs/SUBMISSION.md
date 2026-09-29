# Arc Microgrants — submission draft (Obol)

Fill the `<…>` after the mainnet run. Submit only once the dashboard shows real verdicts.

**Project name:** Obol

**Live deployment on Arc mainnet:** <site URL>  (registry `<REGISTRY>` · agent `<AGENT>`)

**Public repo:** https://github.com/Makabeez/obol

**Builder profile:** https://github.com/Makabeez · X @GeiserJoe2 · Farcaster @makabeez

**What it does (short):**
Obol is a buyer agent for the x402 economy on Arc. It holds a USDC budget, pays paid APIs per call on
Arc mainnet, scores what they actually deliver, moves its spend toward the ones that deliver value per
USDC, and retires the ones that fail. Every verdict is written to an on-chain ReputationRegistry,
with the payment evidence attached, so any other agent can check a seller's record before paying it.

It already pays real third-party sellers on mainnet: Exa search (EIP-3009 `exact`, settled on Arc) and
CRA AGENT's Arc chain-data API (Circle Gateway batched). It caught a real problem on day one: <fill in,
e.g. "CRA's paid data lagged the Arc head by ~5 hours, and Obol scored it down"> — and it retired a
failing endpoint on-chain after three uncharged calls.

**What it uses Arc for:**
- USDC settlement of every paid call on Arc mainnet, over both Circle rails: Facilitator Service
  (`exact`, EIP-3009) and Gateway batched payments.
- USDC gas at ~0.003 per score write: cheap enough to publish every verdict, not just a summary.
- Sub-second finality: a verdict is final before the next purchase decision.
- An on-chain identity in the canonical ERC-8004 IdentityRegistry on Arc (agent #<ID>).
- CCTP v2 (Bridge Kit) to fund the agent from Base.

**Why it's worth taking further:** agents paying agents needs a trust layer that is based on real
payments, not self-reported claims. Next: mirror verdicts into the ERC-8004 ReputationRegistry for
sellers that register agent identities, open the registry to multiple buyer agents (weighted by their
own spend), and ship a drop-in x402 client that checks Obol's record before it pays.

**Safety:** the only key-holding process enforces an allowlist, pinned recipients, Arc-only payments,
per-call and lifetime spend caps (persisted). The contract is small, tested (Foundry, fuzz, mainnet
fork) and unaudited; the agent wallet holds only a few USDC.

**Payout wallet (Arc):** <address that should receive the 500 USDC — use your main wallet, not the agent hot wallet>
