// Withdraw USDC from Circle Gateway back to the agent wallet on Arc (same chain, instant).
//   node --env-file=.env gateway-withdraw.mjs <amount>          # dry run: shows balances
//   node --env-file=.env gateway-withdraw.mjs <amount> --send
import { GatewayClient } from "@circle-fin/x402-batching/client";
const amount = process.argv[2];
const send = process.argv.includes("--send");
if (!amount || !(Number(amount) > 0)) throw new Error("usage: gateway-withdraw.mjs <amount> [--send]");
const gw = new GatewayClient({ chain: "arc", privateKey: process.env.BUYER_PRIVATE_KEY,
                               rpcUrl: process.env.ARC_RPC || "https://rpc.mainnet.arc.io" });
const show = (b) => JSON.stringify(b, (_k, v) => (typeof v === "bigint" ? v.toString() : v));
console.log("before:", show(await gw.getBalances()));
if (!send) { console.log("Dry run. Re-run with --send."); process.exit(0); }
console.log("withdraw:", show(await gw.withdraw(String(amount))));
console.log("after:", show(await gw.getBalances()));
