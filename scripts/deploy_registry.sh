#!/usr/bin/env bash
# Deploy ReputationRegistry v2 to Arc MAINNET with a Foundry keystore account.
#   ./scripts/deploy_registry.sh obol-agent
# Prereqs: forge, `cast wallet import obol-agent --interactive`, a little USDC on Arc for gas.
set -euo pipefail
ACCOUNT="${1:?usage: deploy_registry.sh <foundry-keystore-account>}"
RPC="${ARC_RPC:-https://rpc.mainnet.arc.io}"

CHAIN=$(cast chain-id --rpc-url "$RPC")
[ "$CHAIN" = "5042" ] || { echo "RPC is chain $CHAIN, expected Arc mainnet 5042"; exit 1; }

cd "$(dirname "$0")/../contracts"
[ -d lib/forge-std ] || forge install foundry-rs/forge-std --no-git
forge test -q
forge create src/ReputationRegistry.sol:ReputationRegistry \
  --rpc-url "$RPC" --account "$ACCOUNT" --broadcast | tee /tmp/obol-deploy.log
ADDR=$(grep "Deployed to" /tmp/obol-deploy.log | awk '{print $3}')
echo
echo "REPUTATION_CONTRACT=$ADDR"
echo "Explorer: https://explorer.arc.io/address/$ADDR"
echo "Owner:    $(cast call "$ADDR" 'owner()(address)' --rpc-url "$RPC")"
echo
echo "Optional source verification (Blockscout API; may be blocked by the explorer's bot protection):"
echo "  forge verify-contract $ADDR src/ReputationRegistry.sol:ReputationRegistry --verifier blockscout --verifier-url https://explorer.arc.io/api/"
