// Obol sidecar -- the chain gateway (Arc mainnet).
//
// The ONLY process that holds the agent key or talks to Arc. The Python brain calls it
// over localhost:
//
//   GET  /health                 -> { ok, chainId, address, registry, providers }
//   GET  /balance                -> { wallet, gateway, spent, limit, remaining, payments }
//   POST /pay  {providerId}      -> { ok, paid, httpStatus, amountUsdc, scheme, evidence, latencyMs, body }
//   POST /reputation {providerId, scoreBps, calls, retire, evidence} -> { txHash }
//
// Payments: one x402 client, two rails, Arc mainnet (eip155:5042) only:
//   - "exact" EIP-3009, settled on-chain by the seller's facilitator (e.g. Circle's
//     Facilitator Service) -> PAYMENT-RESPONSE carries the Arc settlement tx hash.
//   - Circle Gateway batched ("GatewayWalletBatched") -> paid from the agent's Gateway
//     balance -> PAYMENT-RESPONSE carries a Gateway transfer id.
// When a seller offers both, exact is preferred: its evidence is an Arc tx anyone can open.
//
// Guardrails (enforced here, never in the Python brain):
//   - allowlist: only providers in PROVIDERS_FILE can be paid, addressed by id, never raw URL
//   - payTo pin: if a provider pins pay_to, any other recipient is refused
//   - network pin (Arc mainnet) + asset pin (Arc USDC 0x3600...)
//   - per-call cap (OBOL_MAX_PRICE) and lifetime cap (OBOL_SPEND_LIMIT); the lifetime
//     spend is persisted to disk so a restart does not reset it
//   - one payment in flight at a time, so accounting is exact
//   - refuses to start if the RPC is not Arc mainnet; listens on 127.0.0.1 only

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import YAML from "yaml";
import {
  createPublicClient, createWalletClient, formatUnits, http as viemHttp,
  keccak256, parseUnits, stringToBytes,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { arc } from "viem/chains";
import { x402Client } from "@x402/core/client";
import { decodePaymentResponseHeader } from "@x402/core/http";
import { ExactEvmScheme } from "@x402/evm/exact/client";
import { wrapFetchWithPayment } from "@x402/fetch";
import { GatewayClient, registerBatchScheme } from "@circle-fin/x402-batching/client";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const ARC_NETWORK = "eip155:5042";
export const ARC_USDC = "0x3600000000000000000000000000000000000000"; // ERC-20 view, 6 decimals
const USDC_DECIMALS = 6;
const GATEWAY_NAME = "GatewayWalletBatched";

// ---- config --------------------------------------------------------------------
const PORT = Number(process.env.SIDECAR_PORT || 8401);
const ARC_RPC = process.env.ARC_RPC || "https://rpc.mainnet.arc.io";
const REGISTRY = process.env.REPUTATION_CONTRACT || "";
const PROVIDERS_FILE = path.resolve(HERE, process.env.PROVIDERS_FILE || "../config/providers.mainnet.yaml");
const LEDGER_FILE = path.resolve(HERE, process.env.SPEND_LEDGER || "./spend-ledger.json");
const SPEND_LIMIT = Number(process.env.OBOL_SPEND_LIMIT || "1.0"); // USDC, lifetime
const MAX_PRICE = Number(process.env.OBOL_MAX_PRICE || "0.01");   // USDC, per call
const MAX_BODY_CHARS = 20_000;

const PK = process.env.BUYER_PRIVATE_KEY || "";
if (!/^0x[0-9a-fA-F]{64}$/.test(PK)) {
  console.error("[obol-sidecar] BUYER_PRIVATE_KEY missing or malformed (need 0x + 64 hex)");
  process.exit(1);
}
if (!(SPEND_LIMIT > 0) || !(MAX_PRICE > 0) || MAX_PRICE > SPEND_LIMIT) {
  console.error("[obol-sidecar] bad caps: need 0 < OBOL_MAX_PRICE <= OBOL_SPEND_LIMIT");
  process.exit(1);
}

const account = privateKeyToAccount(PK);
const publicClient = createPublicClient({ chain: arc, transport: viemHttp(ARC_RPC) });
const walletClient = createWalletClient({ account, chain: arc, transport: viemHttp(ARC_RPC) });
const usdc = (n) => parseUnits(String(n), USDC_DECIMALS);

// ---- provider allowlist ----------------------------------------------------------
function loadProviders() {
  const raw = YAML.parse(fs.readFileSync(PROVIDERS_FILE, "utf8"));
  const map = new Map();
  for (const p of raw?.providers ?? []) {
    if (!p.id || !p.url || !String(p.url).startsWith("https://")) {
      throw new Error(`provider entry needs id + https url: ${JSON.stringify(p)}`);
    }
    map.set(p.id, {
      id: p.id,
      url: p.url,
      method: String(p.method || "GET").toUpperCase(),
      body: p.body ?? null,
      payTo: p.pay_to ? String(p.pay_to).toLowerCase() : null,
      maxPrice: Math.min(Number(p.price_usdc_max ?? p.price_usdc ?? MAX_PRICE), MAX_PRICE),
    });
  }
  if (map.size === 0) throw new Error(`no providers in ${PROVIDERS_FILE}`);
  return map;
}
const PROVIDERS = loadProviders();

// ---- persistent spend ledger -------------------------------------------------------
function readLedger() {
  try {
    const j = JSON.parse(fs.readFileSync(LEDGER_FILE, "utf8"));
    return { spentAtomic: BigInt(j.spentAtomic || "0"), payments: j.payments || 0 };
  } catch {
    return { spentAtomic: 0n, payments: 0 };
  }
}
function writeLedger(l) {
  const tmp = LEDGER_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify({ spentAtomic: l.spentAtomic.toString(), payments: l.payments }, null, 2));
  fs.renameSync(tmp, LEDGER_FILE);
}
let ledger = readLedger();
const LIMIT_ATOMIC = usdc(SPEND_LIMIT);

// ---- x402 client: Arc only, both rails, guarded -----------------------------------
let current = null; // provider being paid (payments are serialized)
let chosen = null;  // requirement the client actually signed

const reqAmount = (r) => BigInt(r?.amount ?? r?.maxAmountRequired ?? "0");

export function guardRequirement(r, provider, spentAtomic, limitAtomic) {
  if (!provider) return false;
  if (r.network !== ARC_NETWORK) return false;
  if (String(r.asset).toLowerCase() !== ARC_USDC) return false;
  if (provider.payTo && String(r.payTo).toLowerCase() !== provider.payTo) return false;
  const amt = reqAmount(r);
  if (amt <= 0n) return false;
  if (amt > usdc(provider.maxPrice)) return false;
  if (spentAtomic + amt > limitAtomic) return false;
  return true;
}

const client = new x402Client((_v, reqs) =>
  // Prefer EIP-3009 exact (evidence = Arc tx), else Gateway batched.
  reqs.find((r) => r.extra?.name !== GATEWAY_NAME) ?? reqs[0],
);
registerBatchScheme(client, {
  signer: account,
  networks: [ARC_NETWORK],
  fallbackScheme: new ExactEvmScheme(account),
});
client.setSpendControls({
  maxAmountPerPayment: false,
  allowedAssets: [{ network: ARC_NETWORK, asset: ARC_USDC, maxAmountPerPayment: usdc(MAX_PRICE).toString() }],
});
client.registerPolicy((_v, reqs) => reqs.filter((r) => guardRequirement(r, current, ledger.spentAtomic, LIMIT_ATOMIC)));
client.onAfterPaymentCreation(async (ctx) => { chosen = ctx?.selectedRequirements ?? null; });

const payFetch = wrapFetchWithPayment(fetch, client);
const gateway = new GatewayClient({ chain: "arc", privateKey: PK, rpcUrl: ARC_RPC });

// ---- helpers ------------------------------------------------------------------------
class HttpError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}
let queue = Promise.resolve();
function serialized(fn) {
  const run = queue.then(fn, fn);
  queue = run.catch(() => {});
  return run;
}
async function readBody(res) {
  const text = await res.text();
  let data = text;
  try { data = JSON.parse(text); } catch { /* keep text */ }
  if (typeof data === "string" && data.length > MAX_BODY_CHARS) data = data.slice(0, MAX_BODY_CHARS);
  return data;
}

// ---- handlers -----------------------------------------------------------------------
async function handlePay({ providerId }) {
  const p = PROVIDERS.get(providerId);
  if (!p) throw new HttpError(403, `provider not in allowlist: ${providerId}`);

  return serialized(async () => {
    current = p;
    chosen = null;
    const t0 = Date.now();
    try {
      let res;
      try {
        res = await payFetch(p.url, {
          method: p.method,
          headers: p.body ? { "content-type": "application/json" } : undefined,
          body: p.body ? JSON.stringify(p.body) : undefined,
          signal: AbortSignal.timeout(45_000),
        });
      } catch (e) {
        // Policy refusals (cap / payTo / network) and transport errors: nothing was paid.
        return { ok: false, paid: false, httpStatus: 0, amountUsdc: 0, scheme: null,
                 evidence: `refused:${String(e?.message || e).slice(0, 160)}`,
                 latencyMs: Date.now() - t0, body: { status: "error", data: null } };
      }
      const latencyMs = Date.now() - t0;
      const data = await readBody(res);

      let settle = null;
      const hdr = res.headers.get("payment-response") || res.headers.get("x-payment-response");
      if (hdr) { try { settle = decodePaymentResponseHeader(hdr); } catch { settle = null; } }

      const paid = Boolean(settle?.success);
      let amountAtomic = 0n;
      if (paid) {
        amountAtomic = settle.amount ? BigInt(settle.amount) : reqAmount(chosen);
        ledger = { spentAtomic: ledger.spentAtomic + amountAtomic, payments: ledger.payments + 1 };
        writeLedger(ledger);
      }
      const scheme = chosen ? (chosen.extra?.name === GATEWAY_NAME ? "gateway-batched" : "exact-eip3009") : null;
      const tx = String(settle?.transaction || "");
      const evidence = paid
        ? (/^0x[0-9a-fA-F]{64}$/.test(tx) ? tx : `gateway:${tx}`)
        : `no-payment:http-${res.status}`;
      return {
        ok: res.ok, paid, httpStatus: res.status,
        amountUsdc: Number(formatUnits(amountAtomic, USDC_DECIMALS)),
        scheme, evidence, latencyMs,
        body: { status: res.ok ? "ok" : "error", data: res.ok ? data : null,
                error: res.ok ? null : (typeof data === "string" ? data.slice(0, 300) : data) },
      };
    } finally {
      current = null;
    }
  });
}

async function handleBalance() {
  let wallet = null;
  let gatewayBal = null;
  try {
    const raw = await publicClient.readContract({
      address: ARC_USDC,
      abi: [{ type: "function", name: "balanceOf", stateMutability: "view",
              inputs: [{ name: "a", type: "address" }], outputs: [{ type: "uint256" }] }],
      functionName: "balanceOf", args: [account.address],
    });
    wallet = Number(formatUnits(raw, USDC_DECIMALS));
  } catch { /* best effort */ }
  try {
    const b = await gateway.getBalances();
    const g = b?.gateway;
    gatewayBal = Number(g?.formattedAvailable ?? g?.formatted ?? g?.available ?? NaN);
    if (Number.isNaN(gatewayBal)) gatewayBal = null;
  } catch { /* best effort */ }
  const spent = Number(formatUnits(ledger.spentAtomic, USDC_DECIMALS));
  return { wallet, gateway: gatewayBal, spent, limit: SPEND_LIMIT,
           remaining: Math.max(0, SPEND_LIMIT - spent), payments: ledger.payments };
}

export const REGISTRY_ABI = [
  { type: "function", name: "recordScore", stateMutability: "nonpayable",
    inputs: [{ type: "bytes32" }, { type: "uint16" }, { type: "uint64" }, { type: "string" }], outputs: [] },
  { type: "function", name: "retire", stateMutability: "nonpayable",
    inputs: [{ type: "bytes32" }, { type: "uint16" }, { type: "uint64" }, { type: "string" }], outputs: [] },
  { type: "function", name: "describe", stateMutability: "nonpayable",
    inputs: [{ type: "bytes32" }, { type: "string" }], outputs: [] },
  { type: "function", name: "uriOf", stateMutability: "view",
    inputs: [{ type: "bytes32" }], outputs: [{ type: "string" }] },
];
export const providerKey = (id) => keccak256(stringToBytes(id));

async function handleReputation({ providerId, scoreBps, calls, retire, evidence }) {
  if (!REGISTRY) throw new HttpError(503, "REPUTATION_CONTRACT not set");
  const p = PROVIDERS.get(providerId);
  if (!p) throw new HttpError(403, `provider not in allowlist: ${providerId}`);
  const score = Math.max(0, Math.min(10_000, Math.round(Number(scoreBps) || 0)));
  const key = providerKey(providerId);
  const ref = String(evidence || "").slice(0, 200);

  return serialized(async () => {
    const uri = await publicClient.readContract({ address: REGISTRY, abi: REGISTRY_ABI, functionName: "uriOf", args: [key] });
    if (!uri) {
      const h = await walletClient.writeContract({ address: REGISTRY, abi: REGISTRY_ABI, functionName: "describe", args: [key, p.url] });
      await publicClient.waitForTransactionReceipt({ hash: h });
    }
    const hash = await walletClient.writeContract({
      address: REGISTRY, abi: REGISTRY_ABI,
      functionName: retire ? "retire" : "recordScore",
      args: [key, score, BigInt(calls ?? 0), ref],
    });
    const rcpt = await publicClient.waitForTransactionReceipt({ hash });
    if (rcpt.status !== "success") throw new HttpError(502, `reputation tx reverted: ${hash}`);
    return { txHash: hash };
  });
}

// ---- HTTP plumbing -----------------------------------------------------------------
function readJson(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => { data += c; if (data.length > 64_000) reject(new HttpError(413, "body too large")); });
    req.on("end", () => {
      if (!data) return resolve({});
      try { resolve(JSON.parse(data)); } catch { reject(new HttpError(400, "bad json")); }
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
    if (req.method === "GET" && req.url === "/health")
      return send(res, 200, { ok: true, chainId: arc.id, address: account.address,
                              registry: REGISTRY || null, providers: [...PROVIDERS.keys()] });
    if (req.method === "GET" && req.url === "/balance") return send(res, 200, await handleBalance());
    if (req.method === "POST" && req.url === "/pay") return send(res, 200, await handlePay(await readJson(req)));
    if (req.method === "POST" && req.url === "/reputation") return send(res, 200, await handleReputation(await readJson(req)));
    return send(res, 404, { ok: false, error: "not found" });
  } catch (e) {
    return send(res, e instanceof HttpError ? e.status : 500, { ok: false, error: String(e?.message || e) });
  }
});

const chainId = await publicClient.getChainId();
if (chainId !== arc.id) {
  console.error(`[obol-sidecar] RPC ${ARC_RPC} is chain ${chainId}, expected Arc mainnet ${arc.id}`);
  process.exit(1);
}
server.listen(PORT, "127.0.0.1", () => {
  console.log(`[obol-sidecar] Arc mainnet (${chainId}) agent ${account.address}`);
  console.log(`[obol-sidecar] registry ${REGISTRY || "(unset)"}; ${PROVIDERS.size} allowlisted providers`);
  console.log(`[obol-sidecar] caps ${MAX_PRICE} USDC/call, ${SPEND_LIMIT} USDC lifetime ` +
              `(spent ${formatUnits(ledger.spentAtomic, USDC_DECIMALS)})`);
});
