// Read-only status: agent balances, Gateway balance, registry owner + provider count.
//   node --env-file=.env status.mjs
import { createPublicClient, formatUnits, http, parseAbi } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { arc } from "viem/chains";
import { GatewayClient } from "@circle-fin/x402-batching/client";

const account = privateKeyToAccount(process.env.BUYER_PRIVATE_KEY);
const rpcUrl = process.env.ARC_RPC || "https://rpc.mainnet.arc.io";
const pub = createPublicClient({ chain: arc, transport: http(rpcUrl) });
const usdc = await pub.readContract({
  address: "0x3600000000000000000000000000000000000000",
  abi: parseAbi(["function balanceOf(address) view returns (uint256)"]),
  functionName: "balanceOf", args: [account.address],
});
console.log("chain      ", await pub.getChainId());
console.log("agent      ", account.address);
console.log("USDC wallet", formatUnits(usdc, 6));
try {
  const b = await new GatewayClient({ chain: "arc", privateKey: process.env.BUYER_PRIVATE_KEY, rpcUrl }).getBalances();
  console.log("gateway    ", JSON.stringify(b, (_k, v) => (typeof v === "bigint" ? v.toString() : v)));
} catch (e) { console.log("gateway     n/a:", e.message); }
const reg = process.env.REPUTATION_CONTRACT;
if (reg) {
  const abi = parseAbi(["function owner() view returns (address)", "function providerCount() view returns (uint256)"]);
  console.log("registry   ", reg, "owner", await pub.readContract({ address: reg, abi, functionName: "owner" }),
              "providers", await pub.readContract({ address: reg, abi, functionName: "providerCount" }));
}
