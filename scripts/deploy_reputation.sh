#!/usr/bin/env bash
# Deploy ReputationRegistry to Arc testnet, then paste the address into .env.
# Requires Foundry (forge) and a funded BUYER_PRIVATE_KEY (gas is USDC on Arc).
set -euo pipefail
: "${ARC_RPC:?set ARC_RPC}"; : "${BUYER_PRIVATE_KEY:?set BUYER_PRIVATE_KEY}"

forge create contracts/ReputationRegistry.sol:ReputationRegistry \
  --rpc-url "$ARC_RPC" \
  --private-key "$BUYER_PRIVATE_KEY" \
  --broadcast

echo
echo "Copy the 'Deployed to:' address into REPUTATION_CONTRACT in .env"
