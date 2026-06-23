// Obol sidecar -- the chain gateway.
//
// Holds the agent wallet and is the ONLY process that touches a private key or talks to
// Arc. Obol's Python brain calls these endpoints over localhost:
//
//   GET  /health
//   GET  /balance                 -> { remaining, spent, limit, gateway? }
//   POST /pay      {url,method,body} -> { ok, status, body, amountUsdc, paymentId }
//   POST /reputation {providerId,scoreBps,calls,retire} -> { txHash }
//
// Payments use Circle's @circle-fin/x402-batching GatewayClient: pay() signs an EIP-3009
// USDC authorization locally and hands it to Gateway, which verifies instantly and settles
// in batches. So a single pay() returns a payment id + amount, NOT an immediate tx hash --
// on-chain proof comes from the batch settlement (seller dashboard) and from the
// ReputationRegistry writes this sidecar makes, which ARE individual Arc txs.
//
// Run:  node payer.mjs        (reads .env in this folder)

import http from "node:http";
import process from "node:process";
import { GatewayClient } from "@circle-fin/x402-batching/client";
import {
  createPublicClient,
  createWalletClient,
  defineChain,
  http as viemHttp,
  keccak256,
  stringToBytes,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";

// ---- config from env -------------------------------------------------------
const PORT = Number(process.env.SIDECAR_PORT || 8401);
const CHAIN = process.env.ARC_CHAIN_NAME || "arcTestnet"; // GatewayClient chain name
const PK = need("BUYER_PRIVATE_KEY");
const ARC_RPC = process.env.ARC_RPC || "";
const ARC_CHAIN_ID = Number(process.env.ARC_CHAIN_ID || 0);
const REGISTRY = process.env.REPUTATION_CONTRACT || "";
const SPEND_LIMIT = process.env.OBOL_SPEND_LIMIT ? Number(process.env.OBOL_SPEND_LIMIT) : null;

function need(k) {
  const v = process.env[k];
  if (!v) {
    console.error(`[obol-sidecar] missing required env ${k}`);
    process.exit(1);
  }
  return v;
}

// ---- Circle Gateway buyer client ------------------------------------------
const gateway = new GatewayClient({ chain: CHAIN, privateKey: PK });

// ---- viem clients for the reputation tx (only if configured) --------------
let walletClient = null;
let publicClient = null;
const account = privateKeyToAccount(PK.startsWith("0x") ? PK : `0x${PK}`);
if (ARC_RPC && ARC_CHAIN_ID) {
  const arc = defineChain({
    id: ARC_CHAIN_ID,
    name: "Arc Testnet",
    nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 6 },
    rpcUrls: { default: { http: [ARC_RPC] } },
  });
  publicClient = createPublicClient({ chain: arc, transport: viemHttp(ARC_RPC) });
  walletClient = createWalletClient({ account, chain: arc, transport: viemHttp(ARC_RPC) });
}

const REGISTRY_ABI = [
  { type: "function", name: "recordScore", stateMutability: "nonpayable",
    inputs: [{ name: "providerId", type: "bytes32" }, { name: "scoreBps", type: "uint16" }, { name: "calls", type: "uint64" }], outputs: [] },
  { type: "function", name: "retire", stateMutability: "nonpayable",
    inputs: [{ name: "providerId", type: "bytes32" }, { name: "scoreBps", type: "uint16" }, { name: "calls", type: "uint64" }], outputs: [] },
];

const providerIdHash = (id) => keccak256(stringToBytes(id));

// ---- spend tracking (a local guard on top of any Gateway-side limit) ------
let totalSpent = 0;

// ---- handlers --------------------------------------------------------------
async function handlePay(req) {
  const { url, method = "GET", body } = req;
  if (!url) throw new HttpError(400, "missing url");
  if (SPEND_LIMIT !== null && totalSpent >= SPEND_LIMIT) {
    throw new HttpError(402, `spend limit reached (${totalSpent}/${SPEND_LIMIT} USDC)`);
  }

  // pay() runs the full x402 cycle: GET -> 402 -> sign EIP-3009 -> settle -> resource.
  // Return shape can vary across SDK versions; we extract defensively. If `body` comes
  // back empty against a known-good endpoint, compare with the reference agent.mts and
  // adjust the field picked here.
  const result = await gateway.pay(url, { method, body });

  const amountUsdc = parseFloat(result.formattedAmount ?? result.amount ?? "0") || 0;
  totalSpent += amountUsdc;

  const payload =
    result.data ?? result.response ?? result.body ?? result.result ?? result.json ?? null;
  const paymentId = result.paymentId ?? result.id ?? result.authorizationId ?? null;

  return {
    ok: true,
    status: result.status ?? 200,
    body: payload,
    amountUsdc,
    paymentId,
  };
}

async function handleBalance() {
  let gatewayBal = null;
  try {
    const fn = gateway.getGatewayBalance ?? gateway.getBalance;
    if (typeof fn === "function") {
      const b = await fn.call(gateway);
      gatewayBal = b?.available ?? b?.formattedAvailable ?? b?.balance ?? b ?? null;
    }
  } catch {
    /* best effort; the local spend guard below is what protects the budget */
  }
  return {
    spent: round6(totalSpent),
    limit: SPEND_LIMIT,
    remaining: SPEND_LIMIT !== null ? round6(SPEND_LIMIT - totalSpent) : null,
    gateway: gatewayBal,
  };
}

async function handleReputation(req) {
  if (!walletClient || !REGISTRY) {
    throw new HttpError(503, "reputation tx not configured (need ARC_RPC, ARC_CHAIN_ID, REPUTATION_CONTRACT)");
  }
  const { providerId, scoreBps, calls, retire } = req;
  if (!providerId) throw new HttpError(400, "missing providerId");
  const args = [providerIdHash(providerId), Number(scoreBps) & 0xffff, BigInt(calls ?? 0)];
  const txHash = await walletClient.writeContract({
    address: REGISTRY,
    abi: REGISTRY_ABI,
    functionName: retire ? "retire" : "recordScore",
    args,
  });
  return { txHash };
}

// ---- tiny HTTP plumbing ----------------------------------------------------
class HttpError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}
const round6 = (n) => Math.round(n * 1e6) / 1e6;

function readJson(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      if (!data) return resolve({});
      try { resolve(JSON.parse(data)); } catch (e) { reject(new HttpError(400, "bad json")); }
    });
    req.on("error", reject);
  });
}
function send(res, status, obj) {
  const s = JSON.stringify(obj, (_k, v) => (typeof v === "bigint" ? v.toString() : v));
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(s) });
  res.end(s);
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url === "/health") return send(res, 200, { ok: true, chain: CHAIN, registry: REGISTRY || null });
    if (req.method === "GET" && req.url === "/balance") return send(res, 200, await handleBalance());
    if (req.method === "POST" && req.url === "/pay") return send(res, 200, await handlePay(await readJson(req)));
    if (req.method === "POST" && req.url === "/reputation") return send(res, 200, await handleReputation(await readJson(req)));
    return send(res, 404, { ok: false, error: "not found" });
  } catch (e) {
    const status = e instanceof HttpError ? e.status : 500;
    return send(res, status, { ok: false, error: e.message });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[obol-sidecar] chain gateway on http://127.0.0.1:${PORT}  (chain=${CHAIN}, registry=${REGISTRY || "none"})`);
  if (SPEND_LIMIT !== null) console.log(`[obol-sidecar] spend limit ${SPEND_LIMIT} USDC`);
});
