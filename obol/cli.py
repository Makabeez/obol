"""Obol CLI.

    python -m obol.cli run --sim --budget 5 --seed 7
    python -m obol.cli run --live          # uses ARC_RPC + Circle creds from env

Sim runs anywhere. Live needs ARC_RPC, REPUTATION_CONTRACT, and Circle agent creds.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys

from .agent import Agent
from .config import load_providers, load_settings
from .reputation.ledger import Ledger


def cmd_run(args) -> int:
    settings = load_settings({
        "mode": "live" if args.live else "sim",
        "budget_usdc": args.budget,
        "min_edge": args.min_edge,
        "max_calls": args.max_calls,
        "decay": args.decay,
        "epsilon": args.epsilon,
    })
    providers = load_providers(args.providers)

    if settings.mode == "live":
        from .arc.live import LiveArcAdapter
        sidecar = os.environ.get("OBOL_SIDECAR_URL", "http://127.0.0.1:8401")
        adapter = LiveArcAdapter(settings.budget_usdc, sidecar_url=sidecar,
                                 explorer_base=settings.explorer_base)
    else:
        from .arc.sim import SimArcAdapter
        adapter = SimArcAdapter(settings.budget_usdc, seed=args.seed)

    queries = itertools.cycle(["price.eth", "vol.btc", "depth.sol", "fees.arc", "oi.hl"])

    def on_event(ev):
        if ev["kind"] == "buy":
            tag = "RETIRED" if ev["retired"] else f"edge {ev['edge']:.3f}"
            print(f"  pay {ev['paid']:.4f} USDC -> {ev['provider']:<22} "
                  f"q={ev['quality']:.2f}  {tag}  bal {ev['balance']:.4f}")
        elif ev["kind"] == "retire":
            print(f"  x  cut {ev['provider']} ({ev['reason']})")
        elif ev["kind"] == "reputation":
            score = ev["score_bps"]
            print(f"  ~  on-chain score {ev['provider']} = {score}/10000")
        elif ev["kind"] == "start":
            print(f"Obol starting -- budget {ev['budget']:.4f} USDC across "
                  f"{ev['providers']} providers\n")

    agent = Agent(providers, adapter, settings, ledger=Ledger(), on_event=on_event)
    summary = agent.run(request_factory=lambda: {"query": next(queries)})

    print("\n--- summary ---")
    print(json.dumps(summary, indent=2))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"summary": summary, "ledger": agent.ledger.snapshot()}, fh, indent=2)
        print(f"\nwrote {args.out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="obol", description="The agent that spends.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the buyer agent")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--sim", action="store_true", help="simulated Arc (default)")
    g.add_argument("--live", action="store_true", help="live Arc + Circle creds")
    r.add_argument("--budget", type=float, default=5.0)
    r.add_argument("--min-edge", dest="min_edge", type=float, default=0.05)
    r.add_argument("--decay", type=float, default=None, help="evidence decay (<1 keeps adapting)")
    r.add_argument("--epsilon", type=float, default=None, help="re-exploration probability")
    r.add_argument("--max-calls", dest="max_calls", type=int, default=400)
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--providers", default=None, help="path to providers.yaml")
    r.add_argument("--out", default=None, help="write summary+ledger json")
    r.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
