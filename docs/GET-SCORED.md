# Get scored by Obol

Obol is a buyer agent on Arc mainnet. It pays x402 sellers per call in USDC, scores what they deliver, and writes every verdict, with the payment as evidence, to a public [ReputationRegistry](https://explorer.arc.io/address/0xDCaf0CcB73fcd81b28099f27A784EA815FB630BE). Other agents read that record before they pay (with [obol-kit](https://github.com/Makabeez/obol-kit)'s `checkBeforePay`).

If you run a paid x402 endpoint on Arc mainnet, Obol will pay it and score it. Free to join; you get paid for every call.

## What you send (2 minutes)

[Open a "Get scored" issue](https://github.com/Makabeez/obol/issues/new?template=get-scored.yml), or DM [@GeiserJoe2](https://x.com/GeiserJoe2) / `Makabeez` on the Canteen Discord, with:

1. **Endpoint URL** (https), and the method. For POST, an example request body.
2. **Price per call.** Up to 0.01 USDC.
3. **Your payTo address.** Obol pins it: if your 402 challenge ever asks to be paid somewhere else, Obol refuses to pay.
4. **What a good response contains**: the JSON keys that must be present and non-empty (optional).

## What your endpoint needs

- Answers unpaid requests with **HTTP 402** and an x402 v2 payment challenge
- Network **Arc mainnet** (`eip155:5042`), asset **Arc USDC** (`0x3600000000000000000000000000000000000000`)
- Scheme **`exact`** (EIP-3009, e.g. via Circle's Facilitator) and/or **Circle Gateway batched**. If you offer both, Obol pays `exact`, because that evidence is an Arc tx anyone can open.

Check your challenge yourself:

```bash
curl -s -D - -o /dev/null https://your.endpoint/path | grep -i payment-required | cut -d' ' -f2 | base64 -d
```

## How you are scored

Only from things Obol can check without trusting you:

| Signal | Weight |
|---|---|
| Paid call returns 2xx with a non-empty payload | 0.55 |
| The fields you promise are present and non-empty | up to 0.20 |
| Latency | up to 0.10 |
| Freshness: an Arc block number in your payload (`blockNumber`, `block`, `head`, `headBlock`, `latestBlock`) vs the live chain head | +0.15, or up to −0.45 if stale |

Scores accumulate across runs (running totals, not the last run). A seller that keeps failing to deliver is **retired** on-chain, with its evidence. Retirement is reversible: fix the endpoint, tell us, and Obol can `reinstate` it.

## What you get

- **Real paid calls** in USDC on Arc mainnet, from an agent with an ERC-8004 identity (#345)
- **A public score with receipts**: every write stores its payment evidence (Arc tx hash or Gateway transfer id) and your endpoint URL, on-chain
- **Listed on the live dashboard**: [obol-arc.vercel.app](https://obol-arc.vercel.app) picks up new sellers from the chain automatically
- **Readable by every agent using obol-kit**: a good score means more buyers

Your provider id is `keccak256(utf8("<your-id>"))`. Check your record any time:

```bash
git clone https://github.com/Makabeez/obol-kit && cd obol-kit && npm i
node -e 'import("./src/reader.mjs").then(async m => console.log(await m.readScore("<your-id>", { withEvidence: true })))'
```

---

## Operator checklist (adding a seller)

For whoever runs Obol's VPS. Every step is small, and step 6 is a commit.

1. **Decode the seller's challenge** and confirm: network `eip155:5042`, asset `0x3600…0000`, the `payTo` they gave you, amount ≤ 0.01 USDC.
   ```bash
   curl -s -D - -o /dev/null <url> | grep -i payment-required | cut -d' ' -f2 | base64 -d
   ```
2. **Add an entry** to `config/providers.mainnet.yaml` (copy an existing block). Pin `pay_to` from the decoded challenge, not from the message. Set `price_usdc` to the advertised price and `price_usdc_max` ~20% above. Tag it `[third-party, exact]` or `[third-party, gateway]`.
3. **Gateway-only seller?** Check the Gateway balance covers it: `cd sidecar && npm run status`.
4. **Reload the allowlist:** `pm2 restart obol-sidecar`
5. **One manual run** to get the first verdict on-chain now rather than at the next cron:
   ```bash
   .venv/bin/python -m obol.cli run --live --budget 0.02 --max-calls 15 --providers config/providers.mainnet.yaml --out runs/latest.json
   ```
6. **Commit and tell people:**
   ```bash
   git commit -am "Providers: add <seller>" && git push
   arc-canteen update-traction   # "ArcOSS: onboarded <seller> (<team>), first paid call + score on Arc mainnet: <tx>"
   ```
   Reply on the issue / DM with the dashboard link and the write tx.
