// Obol registry reader: zero dependencies, plain JSON-RPC + hand ABI decoding.
// Reads the ReputationRegistry on Arc mainnet (chain 5042) directly from the browser.
//
// Why it is built this way:
//  - The public Arc RPC (rpc.mainnet.arc.io) rate-limits bursts. Firing ~20 requests at once
//    (the old Promise.all version) made a few fail and the whole page error out.
//    Requests now go out through a small queue (2 in flight), retry with backoff on rate limits,
//    and fall back to the next mainnet RPC in the list.
//  - Evidence strings live only in event logs. One ranged eth_getLogs (under 10,000 blocks) fetches
//    them all in a single request; if that fails, it falls back to one single-block query per
//    provider (the only log query dRPC's free tier accepts).
//  - Scores come from eth_call and render even if the log lookup fails.
(function (root) {
  const SEL = {
    providerCount: "0x74c83268",
    providerIdAt: "0x17ad993e",
    scoreOf: "0x52db3b28",
    uriOf: "0xf1bb8761",
  };
  const TOPIC_UPDATED = "0x0da346afbbc5bb8bf5c42aa3890f2cfe0a0c44d01672979caeee162348801fae";
  const TOPIC_RETIRED = "0xa3689e6ff5eb814f265b1a876be4c056b14ce4b261a98663538a41c0c8276d3d";
  const ARC_CHAIN_ID = "0x13b2"; // 5042, Arc mainnet

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isRateLimit = (m) => /rate|limit|429|too many|busy|capacity/i.test(m || "");

  // ---- tiny request queue: at most `max` requests in flight ----
  function makeQueue(max) {
    let active = 0;
    const waiting = [];
    const next = () => {
      if (active >= max || !waiting.length) return;
      active++;
      const { fn, res, rej } = waiting.shift();
      fn().then(res, rej).finally(() => { active--; next(); });
    };
    return (fn) => new Promise((res, rej) => { waiting.push({ fn, res, rej }); next(); });
  }

  function makeClient(urls, { concurrency = 2, retries = 3 } = {}) {
    const queue = makeQueue(concurrency);
    let id = 0;
    const stats = { requests: 0, retries: 0, fallbacks: 0, used: new Set() };

    async function once(url, method, params) {
      const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const t = ctl ? setTimeout(() => ctl.abort(), 12000) : null;
      try {
        const r = await fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ jsonrpc: "2.0", id: ++id, method, params }),
          signal: ctl ? ctl.signal : undefined,
        });
        if (r.status === 429) throw new Error("rate limit (HTTP 429)");
        const j = await r.json();
        if (j.error) throw new Error(j.error.message || "rpc error");
        return j.result;
      } finally { if (t) clearTimeout(t); }
    }

    // Try each RPC in order; on a rate limit, back off and retry the same RPC before moving on.
    async function send(method, params, opts = {}) {
      const list = opts.urls || urls;
      let lastErr;
      for (let u = 0; u < list.length; u++) {
        for (let attempt = 0; attempt <= retries; attempt++) {
          try {
            stats.requests++;
            const res = await queue(() => once(list[u], method, params));
            stats.used.add(list[u]);
            if (u > 0) stats.fallbacks++;
            return res;
          } catch (e) {
            lastErr = e;
            if (isRateLimit(e.message) && attempt < retries) {
              stats.retries++;
              await sleep(300 * 2 ** attempt + Math.random() * 200);
              continue;
            }
            break; // non-rate-limit error or out of retries: next RPC
          }
        }
      }
      throw new Error(`${method}: ${lastErr ? lastErr.message : "no RPC available"}`);
    }
    return { send, stats };
  }

  // ---- ABI helpers ----
  const word = (hex, i) => hex.slice(2 + i * 64, 2 + (i + 1) * 64);
  const uint = (hex, i) => BigInt("0x" + word(hex, i));
  const pad = (n) => BigInt(n).toString(16).padStart(64, "0");
  const hx = (n) => "0x" + n.toString(16);
  function decodeStringAt(hex, headWord) {
    const off = Number(uint(hex, headWord)) / 32;
    const len = Number(uint(hex, off));
    const bytesHex = hex.slice(2 + (off + 1) * 64, 2 + (off + 1) * 64 + len * 2);
    const bytes = new Uint8Array((bytesHex.match(/../g) || []).map((b) => parseInt(b, 16)));
    return new TextDecoder().decode(bytes);
  }

  async function readRegistry(urls, registry, { agent } = {}) {
    const rpc = makeClient(Array.isArray(urls) ? urls : [urls]);
    const call = (data) => rpc.send("eth_call", [{ to: registry, data }, "latest"]);

    const chainId = await rpc.send("eth_chainId", []);
    if (chainId !== ARC_CHAIN_ID) throw new Error(`RPC is on chain ${parseInt(chainId, 16)}, not Arc mainnet (5042)`);

    const [countHex, headHex, balHex] = await Promise.all([
      call(SEL.providerCount),
      rpc.send("eth_blockNumber", []),
      agent ? rpc.send("eth_getBalance", [agent, "latest"]).catch(() => null) : null,
    ]);
    const count = Number(BigInt(countHex));
    const head = parseInt(headHex, 16);

    const ids = await Promise.all(Array.from({ length: count }, (_, i) => call(SEL.providerIdAt + pad(i))));
    const rows = await Promise.all(ids.map(async (id) => {
      const idArg = id.slice(2);
      const [rec, uriHex] = await Promise.all([call(SEL.scoreOf + idArg), call(SEL.uriOf + idArg)]);
      return {
        id,
        uri: decodeStringAt(uriHex, 0),
        scoreBps: Number(uint(rec, 0)),
        calls: Number(uint(rec, 1)),
        updatedAt: Number(uint(rec, 2)),
        updatedBlock: Number(uint(rec, 3)),
        retired: uint(rec, 4) === 1n,
        evidenceHash: "0x" + word(rec, 5),
        writeTx: null,
        evidence: null,
      };
    }));

    // ---- evidence from logs (best effort; scores above are already final) ----
    let evidenceOk = true;
    const scored = rows.filter((r) => r.updatedBlock > 0);
    const apply = (logs) => {
      for (const l of logs) {
        const row = rows.find((r) => r.id.toLowerCase() === (l.topics[1] || "").toLowerCase());
        if (!row || parseInt(l.blockNumber, 16) !== row.updatedBlock) continue;
        row.writeTx = l.transactionHash;
        try { row.evidence = decodeStringAt(l.data, 2); } catch { /* leave null */ }
      }
    };
    if (scored.length) {
      const lo = Math.min(...scored.map((r) => r.updatedBlock));
      const hi = Math.max(...scored.map((r) => r.updatedBlock));
      const topics = [[TOPIC_UPDATED, TOPIC_RETIRED]];
      let done = false;
      if (hi - lo < 9999) {
        try {
          apply(await rpc.send("eth_getLogs", [{ address: registry, fromBlock: hx(lo), toBlock: hx(hi), topics }]));
          done = true;
        } catch { /* fall through to per-block */ }
      }
      if (!done) {
        for (const r of scored) {
          try {
            apply(await rpc.send("eth_getLogs", [{
              address: registry, fromBlock: hx(r.updatedBlock), toBlock: hx(r.updatedBlock),
              topics: [[TOPIC_UPDATED, TOPIC_RETIRED], r.id],
            }]));
          } catch { evidenceOk = false; }
        }
      }
    }

    return {
      rows,
      head,
      agentBalance: balHex ? Number(BigInt(balHex) / 10n ** 12n) / 1e6 : null, // native USDC, 18 decimals
      evidenceOk,
      stats: { requests: rpc.stats.requests, retries: rpc.stats.retries, fallbacks: rpc.stats.fallbacks, rpcs: [...rpc.stats.used] },
    };
  }

  root.ObolReader = { readRegistry, makeClient };
  if (typeof module !== "undefined") module.exports = { readRegistry, makeClient };
})(typeof window !== "undefined" ? window : globalThis);
