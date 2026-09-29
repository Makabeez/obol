# Obol on Arc mainnet — runbook

Everything here was checked against Arc mainnet on 2026-09-19. Commands assume the home VPS
(WSL Ubuntu) with the repo at `/mnt/c/Github/obol`. Start every terminal with `source ~/.bashrc`.

## Verified facts this build relies on

| Thing | Value |
|---|---|
| Chain | Arc mainnet, chain id `5042`, RPC `https://rpc.mainnet.arc.io` (alt: `rpc.drpc.mainnet.arc.io`, `rpc.quicknode.mainnet.arc.io`, `rpc.blockdaemon.mainnet.arc.io`) |
| Explorer | `https://explorer.arc.io` |
| USDC | ERC-20 view `0x3600000000000000000000000000000000000000` (6 decimals); native gas balance uses 18 decimals |
| Gas | ~20 gwei floor. Registry deploy ≈ 736k gas (~0.015 USDC); a score write ≈ 75k–160k gas (~0.002–0.003 USDC) |
| `eth_getLogs` | primary RPC rejects ranges ≥ 10,000 blocks (Arc ≈ 0.5 s/block) — the dashboard reads single blocks only |
| Circle Gateway | GatewayWallet `0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE` (Arc, domain 26) |
| CCTP v2 | TokenMessengerV2 `0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d` on Arc and Base; Bridge Kit Base→Arc supports Circle's forwarder (Circle submits the Arc mint) |
| ERC-8004 | IdentityRegistry `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`, ReputationRegistry `0x8004BAa17C55a88189AE136b182e5fdA19dE9b63` (both live on Arc mainnet) |
| Sellers | Exa `/search` (Arc exact + Gateway, $0.007) · CRA AGENT `/v1/paid/*` (Arc Gateway only, $0.0005–0.002, `/selftest/fail` always 500 and never charges) |

## Safety model

- **Fresh hot key** used only by Obol. Hold ≤ ~10 USDC on it. Never your main wallet's key.
- The key lives in two places only: a Foundry keystore (for deploys) and `sidecar/.env` (`chmod 600`, git-ignored).
- The sidecar will only pay providers listed in `config/providers.mainnet.yaml`, only to their pinned `pay_to`,
  only on Arc, only in Arc USDC, at most `OBOL_MAX_PRICE` per call and `OBOL_SPEND_LIMIT` lifetime
  (persisted in `sidecar/spend-ledger.json`, survives restarts). All of this was tested live with an unfunded key.
- The PM2 agent job is **not** auto-restarted, runs every 6 h with a 0.05 USDC budget.
- Contracts are small and tested (7 Foundry tests incl. fuzz, exercised on a mainnet fork) but **unaudited**.

## 0. Sync the repo (check before you overwrite)

```bash
source ~/.bashrc
cd $GH/obol && git fetch && git status --short && git log --oneline -1
# Expect: clean tree, HEAD 38cfb3d "Dashboard: label sim txs honestly…". If not, STOP and check what changed.
git switch -c arc-mainnet
cp /mnt/c/Users/VPS/Downloads/obol-mainnet.zip /tmp/ && rm -rf /tmp/obol-mainnet && unzip -q /tmp/obol-mainnet.zip -d /tmp
rsync -a --delete --exclude .git --exclude '.env' --exclude node_modules /tmp/obol-mainnet/ ./
git status --short   # review: new sidecar, site/, docs/, contracts/src+test, removed testnet leftovers
```

## 1. Harden the repo (before any push)

- GitHub → Settings → Code security: enable **Secret scanning** and **Push protection**.
- Scan history: `docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest detect -s /repo` (or `gitleaks detect`). Must be clean.

## 2. Create the agent key

```bash
cast wallet new                                  # note the address; the key is shown once
cast wallet import obol-agent --interactive      # paste the key, set a password
cp sidecar/.env.example sidecar/.env && chmod 600 sidecar/.env
nano sidecar/.env                                # set BUYER_PRIVATE_KEY (never paste it anywhere else)
```

## 3. Install

```bash
(cd sidecar && npm ci) && (cd tools/bridge && npm ci)
python3 -m pip install -r requirements.txt
python3 -m pytest -q                             # 11 passed
(cd contracts && forge install foundry-rs/forge-std --no-git && forge test)   # 7 passed
```

## 4. Fund: Base → Arc (Circle Bridge Kit, CCTP v2)

From your main wallet on **Base**, send to the agent address: **10 USDC + 0.0002 ETH** (the bridge's Base gas is ~0.000004 ETH).

```bash
cd tools/bridge
node --env-file=../../sidecar/.env bridge-to-arc.mjs 10          # dry run: prints the fee estimate
node --env-file=../../sidecar/.env bridge-to-arc.mjs 10 --send   # burn on Base, Circle mints on Arc
cd ../../sidecar && npm run status                               # USDC wallet ≈ 10 on Arc
```

## 5. Deploy the registry

```bash
cd $GH/obol && ./scripts/deploy_registry.sh obol-agent
nano sidecar/.env        # REPUTATION_CONTRACT=<printed address>
```

## 6. Fund the Gateway rail (for CRA AGENT)

```bash
cd sidecar
node --env-file=.env gateway-deposit.mjs 2           # dry run
node --env-file=.env gateway-deposit.mjs 2 --send
npm run status
```

## 7. Publish the dashboard (Vercel, static, no keys)

```bash
cd $GH/obol && ./scripts/configure_site.sh <REGISTRY> <AGENT_ADDRESS>
```
Commit + push, then Vercel → Add New Project → import `Makabeez/obol` → **Root Directory `site`**,
Framework **Other**, no build command. Note the URL (e.g. `https://obol-arc.vercel.app`), then:

```bash
./scripts/configure_site.sh <REGISTRY> <AGENT_ADDRESS> "" https://<your-site>
git commit -am "site: mainnet addresses" && git push        # Vercel redeploys; agent.json now has real values
```

## 8. Register Obol's ERC-8004 identity

```bash
cd sidecar
node --env-file=.env register-agent.mjs https://<your-site>/agent.json          # dry run, checks the JSON
node --env-file=.env register-agent.mjs https://<your-site>/agent.json --send   # prints agentId + tx
cd .. && ./scripts/configure_site.sh <REGISTRY> <AGENT_ADDRESS> <AGENT_ID> https://<your-site> && git commit -am "site: ERC-8004 id" && git push
```

## 9. First live run

```bash
cd $GH/obol
pm2 start ecosystem.config.js --only obol-sidecar && pm2 save
curl -s 127.0.0.1:8401/health && curl -s 127.0.0.1:8401/balance
python3 -m obol.cli run --live --budget 0.05 --max-calls 30 --providers config/providers.mainnet.yaml --out runs/first.json
```
Expect: Exa paid by EIP-3009 (evidence = Arc tx hash), CRA paid via Gateway, `cra-selftest-fail`
retired after 3 free calls, a registry write per provider. Open the site: the verdicts appear.
Then schedule it: `pm2 start ecosystem.config.js --only obol-agent && pm2 save`.

## 10. Emergency stop / get funds back

```bash
pm2 stop obol-agent obol-sidecar
cd sidecar && npm run status                                     # see the Gateway "available" amount
node --env-file=.env gateway-withdraw.mjs <available> --send     # Gateway -> agent wallet
cast send 0x3600000000000000000000000000000000000000 "transfer(address,uint256)" <YOUR_MAIN_WALLET> <AMOUNT_6_DECIMALS> --account obol-agent --rpc-url https://rpc.mainnet.arc.io
```

## Cost

| Item | USDC |
|---|---|
| Registry deploy | ~0.015 |
| ERC-8004 register | ~0.01 |
| Score writes | ~0.003 each |
| Seller payments | ≤ 0.05 per run, ≤ 1.00 lifetime (cap) |
| Bridge fee (FAST + forwarder) | printed by the dry run in step 4 (CCTP fee on 10 USDC ≈ 0.0004 + forwarder fee) |

Everything else stays on the agent wallet and can be sent back.
