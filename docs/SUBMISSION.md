# Arc Microgrants — submission (Obol)

**Project name:** Obol

**Live deployment on Arc mainnet:** https://obol-arc.vercel.app
(ReputationRegistry `0xDCaf0CcB73fcd81b28099f27A784EA815FB630BE` · agent `0x4a36Df350507d974C882Ac89f402215340d67f55` · ERC-8004 agent #345)

**Public repo:** https://github.com/Makabeez/obol

**Builder profile:** https://github.com/Makabeez · X @GeiserJoe2 · Farcaster @makabeez

**What it does (short):**
Obol is a buyer agent for the x402 economy on Arc. It holds a USDC budget, pays paid APIs per call on
Arc mainnet, scores what they actually deliver, shifts its spend toward the sellers that give the most
value per USDC, and retires sellers that keep failing. Every verdict is written to an on-chain
ReputationRegistry with the payment evidence attached, so any other agent can check a seller's record
before paying it. The dashboard reads the registry straight from the chain in the visitor's browser.

**What is live and verifiable today (first mainnet run, `runs/first.json`):**
- 30 calls across 5 endpoints, 28 paid, 0.0345 USDC spent on calls, all on Arc mainnet.
- Two real third-party sellers paid on both Circle rails: Exa search (EIP-3009 `exact`, settled on
  Arc; payment txs are on the explorer) and CRA AGENT's Arc chain-data API (Circle Gateway batched).
- One deliberately failing endpoint (CRA's self-test, always HTTP 500) was probed: it charged nothing
  and Obol scored it 25/100. A seller that keeps failing is retired on-chain by rule (3 failures);
  that path is implemented and unit-tested but has not triggered on mainnet yet.
- 13 registry transactions (5 provider descriptions, 8 score writes), each score with its evidence
  string: an Arc tx hash, a Gateway transfer id, or `no-payment:http-500`.
- ERC-8004 identity registered in the canonical IdentityRegistry on Arc (agent #345).

**What it uses Arc for:**
- USDC settlement of every paid call on Arc mainnet, over both Circle rails (`exact` / EIP-3009 and
  Gateway batched payments).
- USDC as gas: the 13 registry writes cost about 0.024 USDC in total, cheap enough to publish every
  verdict with its evidence rather than a periodic summary.
- Sub-second finality, so a verdict is final before the next purchase decision.
- The canonical ERC-8004 IdentityRegistry for the agent's on-chain identity.
- CCTP v2 via Bridge Kit to fund the agent from Base (10 USDC bridged, Circle-forwarded mint).

**Why it's worth taking further:** agents paying agents need a trust layer based on real payments, not
self-reported claims. Next: mirror verdicts into the ERC-8004 ReputationRegistry for sellers that
register agent identities, open the registry to multiple buyer agents (weighted by their own spend),
and ship a drop-in x402 client that checks Obol's record before it pays.

**Safety:** the only key-holding process is a local sidecar that enforces an allowlist, pinned
recipients, Arc-only USDC-only payments, a per-call cap and a persisted lifetime cap, one payment in
flight, and it refuses to start off chain 5042. The contract is small, tested (Foundry unit + fuzz +
mainnet fork) and unaudited; the agent wallet holds only a few USDC.
