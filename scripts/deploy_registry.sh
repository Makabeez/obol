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
echo "Verifying source on Sourcify (chain 5042)..."
forge verify-contract "$ADDR" src/ReputationRegistry.sol:ReputationRegistry \
  --chain 5042 --verifier sourcify --watch \
  || echo "Sourcify verification failed; retry the command above. Proof: https://repo.sourcify.dev/5042/$ADDR"
echo "Sourcify: https://repo.sourcify.dev/5042/$ADDR"
echo "Record it in deployments/5042/ (see deployments/5042/ReputationRegistry.json for the format)."
