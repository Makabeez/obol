#!/usr/bin/env bash
# One-time live setup. Run from the repo root.
set -euo pipefail
echo "1) install sidecar deps"
( cd sidecar && npm install )
echo
echo "2) fill sidecar/.env  (BUYER_PRIVATE_KEY, ARC_RPC, ARC_CHAIN_ID, REPUTATION_CONTRACT, OBOL_SPEND_LIMIT)"
echo "   generate a buyer key:  cd sidecar && cast wallet new   (fund the printed address at faucet.circle.com)"
echo
echo "3) deploy ReputationRegistry (viem path):  node sidecar/deploy-registry.mjs   -> paste addr into sidecar/.env"
echo
echo "4) start the sidecar:  cd sidecar && node --env-file=.env payer.mjs   (or via PM2)"
echo "   health check:        curl -s http://127.0.0.1:8401/health"
echo
echo "Then from repo root:   python -m obol.cli run --live --budget 1"
