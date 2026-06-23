<div align="center">

<img src="./assets/banner.svg" alt="Obol — the agent that spends" width="100%" />

# Obol

**The buyer in a room of sellers.**

An autonomous agent with a USDC budget that discovers paid services, pays each per call on Arc, scores what it gets back, and reallocates its budget toward the ones that deliver — cutting off the ones that don't.

[![Live Demo](https://img.shields.io/badge/live-demo-caa253?style=for-the-badge&labelColor=0c1c1a)](https://obol.example.xyz)
[![Reputation Contract](https://img.shields.io/badge/contract-live%20on%20Arc-4fb39a?style=for-the-badge&labelColor=0c1c1a)](https://testnet.arcscan.app/address/0xd893680122269b8127dea0e8ffe00d5bdafe9d70)
[![License](https://img.shields.io/badge/license-MIT-ece6d6?style=for-the-badge&labelColor=0c1c1a)](./LICENSE)
[![Built on Arc](https://img.shields.io/badge/built%20on-Arc%20%C3%97%20Circle-c45a40?style=for-the-badge&labelColor=0c1c1a)](https://docs.arc.network)

![Python](https://img.shields.io/badge/python-3.11-blue)
![x402](https://img.shields.io/badge/x402-pay--per--call-caa253)
![Nanopayments](https://img.shields.io/badge/Circle-Gateway%20%2F%20Nanopayments-4fb39a)
![Thompson Sampling](https://img.shields.io/badge/allocator-Thompson%20sampling-9fb0a4)

</div>

> Every economy mints a smallest coin. The Lepton round is full of teams building the **seller** side — paywalled endpoints, paid agent services, per-article tolls. Obol builds the hard side: the **buyer** that has to decide, on a budget, which of those services are actually worth paying for. It pays them in real test-USDC, keeps an on-chain record of who delivered, and lets that record steer the next coin it spends.

---

## Live on Arc

`ReputationRegistry` is deployed and Obol is writing to it on Arc testnet (chain `5042002`).

| | |
|---|---|
| ReputationRegistry | [`0xd893680122269b8127dea0e8ffe00d5bdafe9d70`](https://testnet.arcscan.app/address/0xd893680122269b8127dea0e8ffe00d5bdafe9d70) |
| Deploy tx | [`0xfde6e531…2a6e16`](https://testnet.arcscan.app/tx/0xfde6e5316ae17880a37f04228ec80d60ae771645a9ad9e6da6590b9b5c2a6e16) |
| First reputation write | [`0xf9f47a52…079d16`](https://testnet.arcscan.app/tx/0xf9f47a52cc1cebfd4adfd447446c66691ab2a683ba1287cc3e9549e6ae079d16) |

The score reads back on-chain exactly as written — `scoreOf(keccak("alpha-feed"))` returns `scoreBps=8900, calls=42, retired=false`. Don't trust the agent; read its verdict off Arc.

---

## Why

The RFBs ask for agents that *discover, evaluate, and pay for services on a budget* (RFB 1), that *sell their work per call* (RFB 2), and that *form agent-to-agent payment networks* (RFB 3). Almost everyone ships the seller. The seller is the easy half — slap HTTP 402 on an endpoint. The buyer is where the agency lives: with finite USDC and noisy providers, **what do you buy, how much, and when do you stop?**

That is a capital-allocation problem. Obol treats every provider like a position: size into the ones with edge, keep probing the cheap unknowns in case they're alpha, and cut the losers the moment you're confident they're losers. The smartness is the allocation, not the plumbing.

It also produces a public good. Each thing Obol learns — *this provider delivers, that one takes your money and returns nothing* — gets written to an on-chain `ReputationRegistry` any other agent can read before it pays. One buyer's experience, priced in USDC, posted for the whole network.

## Architecture

```
                          +-----------------------------+
   request -------------> |          Obol agent         |
                          |  (buy / evaluate / record)  |
                          +--------------+--------------+
                                         |
              select (Thompson on edge)  |  observe (quality, cost)
                                         v
                          +-----------------------------+
                          |        ValueBandit          |   <- the differentiator
                          |  per-provider Beta posterior|
                          |  edge gate + delivery gate  |
                          +--------------+--------------+
                                         |
                           pay_and_fetch | (x402)
                                         v
   +--------------+        +-----------------------------+        +------------------+
   |  Evaluator   | <----- |         Arc adapter         | -----> |  Reputation      |
   | prog + LLM*  | result | sim:// now   live: Circle    | score  |  Registry (Arc)  |
   +--------------+        | Wallets / Gateway / x402     |        +------------------+
                          +-----------------------------+
        * LLM judge is Anthropic-only (wallet-adjacent decision; never a 3rd-party model).
```

One seam — the **Arc adapter** — separates the brain from the chain. `SimArcAdapter` runs anywhere with no creds, so the allocation logic is built and demoed today. `LiveArcAdapter` wires the identical interface to a Circle agent wallet + x402 settlement on Arc. Flipping `--sim` to `--live` changes no call sites.

## Tech stack

| Layer            | Choice                                              | Why |
|------------------|-----------------------------------------------------|-----|
| Allocation       | Thompson sampling over value-per-USDC (Beta posteriors) | Explore/exploit under a hard budget; the agency the rubric weights |
| Adaptivity       | Evidence decay + epsilon re-exploration             | Re-evaluates a live market; tracks providers whose quality drifts |
| Cut-off          | Delivery gate (non-decaying) + edge gate            | Retires scammers that take payment and return nothing; starves the merely-mediocre |
| Payments         | Circle Agent Stack — Wallets, Gateway/Nanopayments, x402 | Sub-cent USDC settlement on Arc |
| Settlement       | Arc testnet, USDC gas, sub-second finality          | Makes a per-call buy economical |
| Reputation       | `ReputationRegistry.sol` (Arc)                       | Public, readable track record per provider |
| Evaluation       | Programmatic + optional Anthropic judge             | Free signal first; LLM only for substance, Anthropic-only |
| Dashboard        | FastAPI + a single self-driving page                | The hero is the agent spending, live |

## Flow

1. **Discover** — load the provider registry (x402 endpoints). Live: pulled from the services other teams ship.
2. **Select** — `ValueBandit` samples each live provider's likely quality, divides by its advertised price, and buys from the best draw.
3. **Pay** — the Arc adapter settles the provider's 402 challenge in USDC (gasless batch via Gateway), gets the payload.
4. **Evaluate** — score the result in `[0,1]`: programmatic by default, Anthropic judge for substance.
5. **Update** — feed the score back to the bandit. Append to the ledger. Periodically publish the provider's running quality score on-chain.
6. **Cut** — once confident a provider's edge or delivery is below the floor, retire it. Repeat until the budget is dry.

## Smart contract

`ReputationRegistry.sol` — delivered-quality scores (0–10000 bps), written by Obol as it spends, readable by anyone.

```solidity
function recordScore(bytes32 providerId, uint16 scoreBps, uint64 calls) external onlyOwner;
function retire(bytes32 providerId, uint16 scoreBps, uint64 calls) external onlyOwner;
function scoreOf(bytes32 providerId) external view returns (Record memory);
```

```solidity
event ReputationUpdated(bytes32 indexed providerId, uint16 scoreBps, uint64 calls);
event ProviderRetired(bytes32 indexed providerId, uint16 scoreBps, uint64 calls);
```

## Local dev

```bash
pip install -r requirements.txt

# run the buyer against simulated sellers (no creds needed)
python -m obol.cli run --sim --budget 2 --seed 7 --min-edge 0.05

# watch it spend live in the browser (self-driving sim agent)
python -m uvicorn dashboard.server:app --port 8099
# open http://localhost:8099

# tests (the allocator is the differentiator, so it carries the real ones)
python -m pytest -q
```

What a sim run shows: the agent concentrates spend on the cheap high-quality provider, probes the unknowns, and retires the rug-seller (takes payment, delivers nothing) after a handful of calls — a real decision, not a script.

## Deployment (live on Arc)

Live mode runs through the **sidecar** (`sidecar/payer.mjs`) -- the only process that
holds a key or touches Arc. The Python brain POSTs jobs to it over localhost.

```bash
cd sidecar && npm install
cast wallet new                     # generate a buyer key -> fund the address at faucet.circle.com
cp .env.example .env                # fill BUYER_PRIVATE_KEY, ARC_RPC, ARC_CHAIN_ID, OBOL_SPEND_LIMIT
node deploy-registry.mjs            # deploy ReputationRegistry -> paste address into .env
node --env-file=.env payer.mjs      # start the sidecar on :8401 (leave running / use PM2)
curl -s http://127.0.0.1:8401/health

# then, from the repo root:
python -m obol.cli run --live --budget 1
```

The sidecar exposes `GET /health`, `GET /balance`, `POST /pay {url,method,body}`, and
`POST /reputation {providerId,scoreBps,calls,retire}`. Payments use Circle's
`@circle-fin/x402-batching` GatewayClient: `pay()` signs an EIP-3009 USDC authorization
and hands it to Gateway, which settles in batches -- so a call returns a `paymentId` and
amount, while the **ReputationRegistry writes are individual on-chain Arc txs**. Per-call
payment proof comes from the Gateway / seller dashboard; the reputation ledger is your
on-chain artifact.

Add real endpoints to `config/providers.yaml` as you find them -- start with the
`circlefin/arc-nanopayments` reference seller, then each cross-team service from the
Canteen / Arc Discords. Process management on the VPS uses PM2 (`ecosystem.config.js`).

## Attribution

Built for the **Lepton Agents Hackathon** (Canteen × Circle on Arc). Uses the Circle Agent Stack — Wallets, Gateway/Nanopayments, x402 — and the `circlefin/arc-nanopayments` reference as the live x402 counterpart. Carries forward the agentic-payments work from the Agora round; the delta here — the buyer-side allocator, the reputation registry, and live cross-team payments — is what's new.

## License

MIT — see [LICENSE](./LICENSE).
