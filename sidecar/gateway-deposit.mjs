// Deposit USDC from the agent wallet into Circle Gateway on Arc mainnet.
// Needed only for sellers that accept Gateway batched payments (e.g. CRA AGENT).
//
//   node --env-file=.env gateway-deposit.mjs 2          # shows balances, dry run
//   node --env-file=.env gateway-deposit.mjs 2 --send   # approve + deposit
import { GatewayClient } from "@circle-fin/x402-batching/client";

const MAX_DEPOSIT = 5;
const amount = process.argv[2];
const send = process.argv.includes("--send");
if (!amount || !(Number(amount) > 0) || Number(amount) > MAX_DEPOSIT)
  throw new Error(`usage: gateway-deposit.mjs <amount 0<x<=${MAX_DEPOSIT}> [--send]`);

const gw = new GatewayClient({
  chain: "arc",
  privateKey: process.env.BUYER_PRIVATE_KEY,
  rpcUrl: process.env.ARC_RPC || "https://rpc.mainnet.arc.io",
});
const show = (b) => JSON.stringify(b, (_k, v) => (typeof v === "bigint" ? v.toString() : v));
console.log("before:", show(await gw.getBalances()));
if (!send) { console.log("Dry run. Re-run with --send."); process.exit(0); }
const r = await gw.deposit(String(amount));
console.log("deposit:", show(r));
console.log(`https://explorer.arc.io/tx/${r.depositTxHash}`);
console.log("after:", show(await gw.getBalances()));
