// Deploy ReputationRegistry.sol to Arc testnet with viem.
//
// Easiest path is Foundry:
//   forge create contracts/ReputationRegistry.sol:ReputationRegistry \
//     --rpc-url $ARC_RPC --private-key $BUYER_PRIVATE_KEY --broadcast
//
// This script is the no-Foundry fallback: it reads a compiled artifact
// (abi + bytecode) and deploys it. Produce the artifact with solc:
//   solc --combined-json abi,bin contracts/ReputationRegistry.sol > sidecar/artifact.json
//
// Then:  ARC_RPC=... ARC_CHAIN_ID=... BUYER_PRIVATE_KEY=... node deploy-registry.mjs

import fs from "node:fs";
import process from "node:process";
import { createPublicClient, createWalletClient, defineChain, http as viemHttp } from "viem";
import { privateKeyToAccount } from "viem/accounts";

const ARC_RPC = req("ARC_RPC");
const ARC_CHAIN_ID = Number(req("ARC_CHAIN_ID"));
const PK = req("BUYER_PRIVATE_KEY");
const ARTIFACT = process.env.ARTIFACT || "./artifact.json";

function req(k) { const v = process.env[k]; if (!v) { console.error(`missing env ${k}`); process.exit(1); } return v; }

const combined = JSON.parse(fs.readFileSync(ARTIFACT, "utf8"));
// solc --combined-json shape: { contracts: { "path:Name": { abi, bin } } }
const key = Object.keys(combined.contracts).find((k) => k.endsWith(":ReputationRegistry"));
const c = combined.contracts[key];
const abi = typeof c.abi === "string" ? JSON.parse(c.abi) : c.abi;
const bytecode = c.bin.startsWith("0x") ? c.bin : `0x${c.bin}`;

const arc = defineChain({
  id: ARC_CHAIN_ID,
  name: "Arc Testnet",
  nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 6 },
  rpcUrls: { default: { http: [ARC_RPC] } },
});
const account = privateKeyToAccount(PK.startsWith("0x") ? PK : `0x${PK}`);
const wallet = createWalletClient({ account, chain: arc, transport: viemHttp(ARC_RPC) });
const pub = createPublicClient({ chain: arc, transport: viemHttp(ARC_RPC) });

const hash = await wallet.deployContract({ abi, bytecode, args: [] });
console.log("deploy tx:", hash);
const receipt = await pub.waitForTransactionReceipt({ hash });
console.log("ReputationRegistry deployed at:", receipt.contractAddress);
console.log("\nadd to .env:\nREPUTATION_CONTRACT=" + receipt.contractAddress);
