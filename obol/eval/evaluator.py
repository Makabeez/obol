"""Evaluator -- turn a seller's response into a quality score in [0,1].

Deliberately thin. Per the focus rule, the intelligence lives in the allocator, not here.
Most of the signal is programmatic and free: did we get a payload, is it well-formed, does
it answer the request. An optional content scorer handles the cases where quality is about
*substance* (an article, a data answer) rather than shape -- and because that score steers
real spending, it routes through Anthropic only, never a third-party LLM.
"""

from __future__ import annotations

import os


def programmatic_score(request: dict, body: dict | None) -> float:
    """Cheap, deterministic quality signal. No network, no spend."""
    if not body or body.get("status") != "ok":
        return 0.0
    data = body.get("data")
    if data is None:
        return 0.0  # took payment, returned nothing useful (dropout)
    score = 0.5
    # Reward richer payloads and intent match; clamp to [0,1].
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


def content_score(request: dict, body: dict | None, model: str) -> float | None:
    """Optional Anthropic-only judge for substance. Returns None if unavailable.

    Wallet-adjacent: this score moves USDC, so it must not pass through any third-party
    provider. Uses the Anthropic SDK directly with the model from settings.eval_model.
    """
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
        prompt = (
            "Rate how well the RESPONSE satisfies the REQUEST on a 0.0-1.0 scale. "
            "Reply with only the number.\n\n"
            f"REQUEST: {request}\n\nRESPONSE: {body.get('data')}"
        )
        msg = client.messages.create(
            model=model,
            max_tokens=8,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return max(0.0, min(1.0, float(text.strip())))
    except Exception:
        return None


def evaluate(request: dict, body: dict | None, model: str = "") -> float:
    llm = content_score(request, body, model) if model else None
    if llm is not None:
        return llm
    return programmatic_score(request, body)
