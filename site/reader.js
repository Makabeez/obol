// Obol registry reader -- zero dependencies, plain JSON-RPC + hand ABI decoding.
// Shared by index.html (browser) and the node test.
(function (root) {
  const SEL = {
    providerCount: "0x74c83268",
    providerIdAt: "0x17ad993e",
    scoreOf: "0x52db3b28",
    uriOf: "0xf1bb8761",
    owner: "0x8da5cb5b",
  };
  const TOPIC_UPDATED = "0x0da346afbbc5bb8bf5c42aa3890f2cfe0a0c44d01672979caeee162348801fae";
  const TOPIC_RETIRED = "0xa3689e6ff5eb814f265b1a876be4c056b14ce4b261a98663538a41c0c8276d3d";

  let rpcId = 0;
  async function rpc(url, method, params) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: ++rpcId, method, params }),
    });
    const j = await r.json();
    if (j.error) throw new Error(`${method}: ${j.error.message}`);
    return j.result;
  }
  const call = (url, to, data) => rpc(url, "eth_call", [{ to, data }, "latest"]);

  const word = (hex, i) => hex.slice(2 + i * 64, 2 + (i + 1) * 64);
  const uint = (hex, i) => BigInt("0x" + word(hex, i));
  const pad = (n) => BigInt(n).toString(16).padStart(64, "0");
  function decodeStringAt(hex, headWord) {
    const off = Number(uint(hex, headWord)) / 32;
    const len = Number(uint(hex, off));
    const bytesHex = hex.slice(2 + (off + 1) * 64, 2 + (off + 1) * 64 + len * 2);
    const bytes = new Uint8Array(bytesHex.match(/../g)?.map((b) => parseInt(b, 16)) ?? []);
    return new TextDecoder().decode(bytes);
  }

  async function readRegistry(url, registry) {
    const count = Number(BigInt(await call(url, registry, SEL.providerCount)));
    const ids = await Promise.all(
      Array.from({ length: count }, (_, i) => call(url, registry, SEL.providerIdAt + pad(i))));
    const rows = await Promise.all(ids.map(async (id) => {
      const idArg = id.slice(2);
      const [rec, uriHex] = await Promise.all([
        call(url, registry, SEL.scoreOf + idArg),
        call(url, registry, SEL.uriOf + idArg),
      ]);
      const row = {
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
      if (row.updatedBlock > 0) {
        const blk = "0x" + row.updatedBlock.toString(16);
        const logs = await rpc(url, "eth_getLogs", [{
          address: registry, fromBlock: blk, toBlock: blk,
          topics: [[TOPIC_UPDATED, TOPIC_RETIRED], id],
        }]);
        const last = logs[logs.length - 1];
        if (last) {
          row.writeTx = last.transactionHash;
          row.evidence = decodeStringAt(last.data, 2);
        }
      }
      return row;
    }));
    const owner = "0x" + (await call(url, registry, SEL.owner)).slice(-40);
    return { owner, rows };
  }

  root.ObolReader = { readRegistry };
  if (typeof module !== "undefined") module.exports = { readRegistry };
})(typeof window !== "undefined" ? window : globalThis);
