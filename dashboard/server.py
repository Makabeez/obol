"""Obol dashboard server.

Serves a live view of the agent spending. In sim mode it runs a throttled agent in a
background thread so the dashboard moves on its own -- you watch it discover the gems and
cut the rugs in real time. Point a browser at http://localhost:8099.

    python -m dashboard.server                 # sim, self-driving demo
    OBOL_LEDGER=/path/run.jsonl python ...      # (live) tail a ledger the agent writes

Kept intentionally small: one /api/state endpoint, one static page.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from obol.agent import Agent
from obol.arc.sim import SimArcAdapter
from obol.config import load_providers, load_settings
from obol.reputation.ledger import Ledger

HERE = Path(__file__).resolve().parent
app = FastAPI(title="Obol")

_state = {"events": [], "summary": {}, "started": time.time()}
_lock = threading.Lock()


def _run_agent_forever(throttle_s: float = 0.45, budget: float = 2.0) -> None:
    """Self-driving sim: loop sessions so the dashboard always has live motion."""
    queries = ["price.eth", "vol.btc", "depth.sol", "fees.arc", "oi.hl"]
    qi = 0
    while True:
        providers = load_providers()
        settings = load_settings({"budget_usdc": budget, "max_calls": 1200, "min_edge": 0.05})
        adapter = SimArcAdapter(budget, seed=None)
        ledger = Ledger()

        def on_event(ev):
            with _lock:
                _state["events"].append(ev)
                _state["events"][:] = _state["events"][-60:]
                _state["summary"] = {
                    "balance": adapter.balance_usdc(),
                    "budget": budget,
                    "spend": ledger.total_spend(),
                    "value": ledger.total_value(),
                    "providers": ledger.by_provider(),
                }

        agent = Agent(providers, adapter, settings, ledger=ledger, on_event=on_event)
        # Step manually so we can throttle for a watchable pace.
        agent._emit("start", budget=adapter.balance_usdc(), providers=len(providers))
        while agent._call_count < settings.max_calls:
            ok = agent.step({"query": queries[qi % len(queries)]})
            qi += 1
            if not ok:
                break
            time.sleep(throttle_s)
        time.sleep(2.5)  # brief pause, then a fresh session


@app.on_event("startup")
def _start():
    t = threading.Thread(target=_run_agent_forever, daemon=True)
    t.start()


@app.get("/api/state")
def state():
    with _lock:
        return {"events": _state["events"][::-1], "summary": _state["summary"]}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
