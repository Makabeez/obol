"""Evaluator -- turn a seller's response into a quality score in [0,1].

Two paths:
  * sim providers (url sim://...): the original programmatic scorer on the simulator's
    {"status","data"} shape.
  * live providers (Arc mainnet): a scorer for real seller responses, built from signals
    Obol can check without trusting the seller:
      - did the paid call return 2xx with a non-empty payload         (0.55)
      - are the fields this provider promises present and non-empty   (up to 0.20)
      - latency                                                        (up to 0.10)
      - freshness: any Arc block number in the payload vs the live
        chain head -- stale chain data is penalised hard               (+0.15 / up to -0.45)

An optional Anthropic-only judge handles substance (wallet-adjacent: this score moves
USDC, so it never routes through a third-party model).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

_HEAD_CACHE: dict[str, float] = {"head": 0.0, "at": 0.0}
BLOCK_KEYS = ("blockNumber", "block", "head", "headBlock", "latestBlock")


def programmatic_score(request: dict, body: dict | None) -> float:
    """Sim scorer. Cheap, deterministic. No network, no spend."""
    if not body or body.get("status") != "ok":
        return 0.0
    data = body.get("data")
    if data is None:
        return 0.0
    score = 0.5
    if isinstance(data, dict):
        if "score" in data:
            try:
                score = float(data["score"])
            except (TypeError, ValueError):
                score = 0.5
        fields = data.get("fields", 0)
        if isinstance(fields, (int, float)) and fields > 0:
            score = min(1.0, score + min(0.15, fields * 0.01))
    if body.get("query") and request.get("query") and body["query"] == request["query"]:
        score = min(1.0, score + 0.05)
    return max(0.0, min(1.0, score))


def _arc_head() -> int | None:
    now = time.time()
    if _HEAD_CACHE["head"] and now - _HEAD_CACHE["at"] < 20:
        return int(_HEAD_CACHE["head"])
    rpc = os.environ.get("ARC_RPC", "https://rpc.mainnet.arc.io")
    try:
        req = urllib.request.Request(rpc, method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber",
                                                      "params": []}).encode())
        with urllib.request.urlopen(req, timeout=10) as r:
            head = int(json.loads(r.read())["result"], 16)
        _HEAD_CACHE.update(head=head, at=now)
        return head
    except Exception:
        return None


def _find_block(obj, depth: int = 0) -> int | None:
    if depth > 4:
        return None
    if isinstance(obj, dict):
        for k in BLOCK_KEYS:
            v = obj.get(k)
            if isinstance(v, int) and v > 1_000_000:
                return v
            if isinstance(v, str) and v.isdigit() and int(v) > 1_000_000:
                return int(v)
        for v in obj.values():
            b = _find_block(v, depth + 1)
            if b:
                return b
    elif isinstance(obj, list):
        for v in obj[:5]:
            b = _find_block(v, depth + 1)
            if b:
                return b
    return None


def _nonempty(v) -> bool:
    return v not in (None, "", [], {})


def live_score(provider, body: dict | None, latency_ms: float = 0.0) -> float:
    if not body or body.get("status") != "ok":
        return 0.0
    data = body.get("data")
    if not _nonempty(data):
        return 0.0
    score = 0.55
    expect = list(getattr(provider, "expect", []) or [])
    if expect and isinstance(data, dict):
        score += 0.20 * sum(1 for k in expect if _nonempty(data.get(k))) / len(expect)
    elif isinstance(data, (dict, list)) and len(data) >= 2:
        score += 0.20
    if latency_ms and latency_ms < 1500:
        score += 0.10
    elif latency_ms and latency_ms < 5000:
        score += 0.05
    block = _find_block(data)
    head = _arc_head() if block else None
    if block and head:
        lag_s = max(0, head - block) * 0.5  # Arc ~0.5 s blocks
        if lag_s <= 120:
            score += 0.15
        else:  # stale: -0.15 at 2 min growing to -0.45 at >= 1 h
            score -= min(0.45, 0.15 + 0.30 * min(1.0, lag_s / 3600))
    return max(0.0, min(1.0, score))


def content_score(request: dict, body: dict | None, model: str) -> float | None:
    """Optional Anthropic-only judge for substance. Returns None if unavailable."""
    if not model or not body or body.get("data") is None:
        return None
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        client = anthropic.Anthropic()
        prompt = ("Rate how well the RESPONSE satisfies the REQUEST on a 0.0-1.0 scale. "
                  "Reply with only the number.\n\n"
                  f"REQUEST: {request}\n\nRESPONSE: {str(body.get('data'))[:4000]}")
        msg = client.messages.create(model=model, max_tokens=8,
                                     messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return max(0.0, min(1.0, float(text.strip())))
    except Exception:
        return None


def evaluate(request: dict, body: dict | None, model: str = "", provider=None,
             latency_ms: float = 0.0) -> float:
    llm = content_score(request, body, model) if model else None
    if llm is not None:
        return llm
    if provider is not None and not str(getattr(provider, "url", "")).startswith("sim://"):
        return live_score(provider, body, latency_ms)
    return programmatic_score(request, body)
