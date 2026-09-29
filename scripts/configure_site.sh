#!/usr/bin/env bash
# Write deployed values into the dashboard, README and ERC-8004 metadata.
#   ./scripts/configure_site.sh <registry> <agentAddress> [erc8004AgentId] [siteUrl]
# Safe to re-run: the dashboard config is rewritten each time; README/agent.json
# placeholders are filled the first time a value is supplied.
set -euo pipefail
REG="${1:?registry}"; AGENT="${2:?agent}"; ID="${3:-}"; SITE="${4:-}"
for a in "$REG" "$AGENT"; do [[ "$a" =~ ^0x[0-9a-fA-F]{40}$ ]] || { echo "bad address: $a"; exit 1; }; done
[[ -z "$ID" || "$ID" =~ ^[0-9]+$ ]] || { echo "bad agent id: $ID"; exit 1; }
[[ -z "$SITE" || "$SITE" =~ ^https://[A-Za-z0-9.-]+(/.*)?$ ]] || { echo "bad site url: $SITE"; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
sed -i -E \
  -e "s@registry: \"[^\"]*\"@registry: \"$REG\"@" \
  -e "s@agent: \"[^\"]*\"@agent: \"$AGENT\"@" \
  -e "s@agentId: \"[^\"]*\"@agentId: \"$ID\"@" "$ROOT/site/index.html"
for f in "$ROOT/README.md" "$ROOT/site/agent.json"; do
  sed -i -e "s@__REGISTRY__@$REG@g" -e "s@__AGENT__@$AGENT@g" "$f"
  [ -n "$ID" ] && sed -i -e "s@__AGENT_ID__@$ID@g" "$f"
  [ -n "$SITE" ] && sed -i -e "s@__SITE_URL__@${SITE%/}@g" "$f"
done
grep -nE 'registry: "|agent: "|agentId: "' "$ROOT/site/index.html"
echo "placeholders left:"; grep -c "__[A-Z_]*__" "$ROOT/README.md" "$ROOT/site/agent.json" || true
