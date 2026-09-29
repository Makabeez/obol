// One-shot funding: bridge USDC Base -> Arc mainnet with Circle Bridge Kit (CCTP v2).
//
// Uses Circle's forwarder (Orbit) so the Arc-side mint is submitted by Circle: the agent
// wallet needs NO gas on Arc to receive. The source wallet needs USDC + a little ETH on Base.
//
//   cd tools/bridge && npm ci
//   node --env-file=../../sidecar/.env bridge-to-arc.mjs 10          # estimate only
//   node --env-file=../../sidecar/.env bridge-to-arc.mjs 10 --send   # really bridge
//
// Reads BUYER_PRIVATE_KEY (the fresh Obol agent key). Bridges to that same address on Arc.
import { BridgeKit } from "@circle-fin/bridge-kit";
import { createViemAdapterFromPrivateKey } from "@circle-fin/adapter-viem-v2";
import { privateKeyToAccount } from "viem/accounts";

const MAX_BRIDGE = 25; // hard ceiling: this is a hot wallet
const amount = process.argv[2];
const send = process.argv.includes("--send");
const pk = process.env.BUYER_PRIVATE_KEY;

if (!/^0x[0-9a-fA-F]{64}$/.test(pk || "")) throw new Error("BUYER_PRIVATE_KEY missing");
if (!amount || !(Number(amount) > 0) || Number(amount) > MAX_BRIDGE)
  throw new Error(`usage: bridge-to-arc.mjs <amount 0<x<=${MAX_BRIDGE}> [--send]`);

const recipient = privateKeyToAccount(pk).address;
const kit = new BridgeKit();
const adapter = createViemAdapterFromPrivateKey({ privateKey: pk });
const params = {
  from: { adapter, chain: "Base" },
  to: { recipientAddress: recipient, chain: "Arc", useForwarder: true },
  amount: String(amount),
  config: { transferSpeed: "FAST" },
};

console.log(`Base -> Arc  ${amount} USDC  to ${recipient}`);
const est = await kit.estimate(params);
console.log("estimate:", JSON.stringify(est, (_k, v) => (typeof v === "bigint" ? v.toString() : v), 2));
if (!send) {
  console.log("\nDry run only. Re-run with --send to bridge.");
  process.exit(0);
}
const result = await kit.bridge(params);
console.log("result:", JSON.stringify(result, (_k, v) => (typeof v === "bigint" ? v.toString() : v), 2));
console.log(`\nCheck: https://explorer.arc.io/address/${recipient}`);
