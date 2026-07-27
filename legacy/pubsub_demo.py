"""LEGACY -- proves the event bus against the superseded pot/split-steal
case game (the project is Life Rolls / Liar's Dice now, see
design/DESIGN.md; tournament_pubsub_demo.py at the repo root is the
current game's equivalent proof script). The EventBus/watcher pattern
itself (engine/eventbus.py) is still very much current -- this file just
exercises it against the older game's events.

    python legacy/pubsub_demo.py
    python legacy/pubsub_demo.py --seed 7
    python legacy/pubsub_demo.py --live
"""
from __future__ import annotations
import argparse
import asyncio
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.cases import CASE_LIBRARY
from engine.eventbus import EventBus
from engine.live_case import run_case_on_bus
from engine.negotiation import OllamaNegotiator, ScriptedNegotiator, TEXT_MODELS
from engine.personas import draw_cast
from engine.watchers import AudienceWatcher, HostWatcher, StateWatcher, StatsWatcher


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError):
        return False


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    case = rng.choice(CASE_LIBRARY)
    persona_a, persona_b = draw_cast(2, rng)

    use_live = args.live and _ollama_reachable()
    if use_live:
        model_a, model_b = rng.sample(TEXT_MODELS, k=2)
        negotiator_a, negotiator_b = OllamaNegotiator(rng, model_a), OllamaNegotiator(rng, model_b)
    else:
        negotiator_a, negotiator_b = ScriptedNegotiator(rng), ScriptedNegotiator(rng)

    loop = asyncio.get_event_loop()
    bus = EventBus(loop)

    audience, host, stats, state = AudienceWatcher(), HostWatcher(), StatsWatcher(), StateWatcher()
    watchers = {"audience": audience, "host": host, "stats": stats, "state": state}

    # Subscribe every watcher's queue SYNCHRONOUSLY, before the case
    # starts publishing -- this is the fix for the race a lazy subscribe()
    # inside each watcher's own coroutine would otherwise have (see
    # engine/watchers.py's _drain docstring).
    queues = {name: bus.subscribe() for name in watchers}
    tasks = [asyncio.create_task(w.run(queues[name])) for name, w in watchers.items()]

    print(f"Case: {case.name}  [{case.framework}]  --  4 watchers subscribed, none aware of each other\n")
    result = await run_case_on_bus(case, persona_a, persona_b, negotiator_a, negotiator_b,
                                    bus, debate_rounds=2 if use_live else 1)
    await asyncio.gather(*tasks)

    print(f"NEGOTIATION RESULT: {persona_a.archetype_name}={result.decision_a.upper()}  "
          f"{persona_b.archetype_name}={result.decision_b.upper()}\n")

    print("AUDIENCE watcher saw only public events:")
    for r in audience.reactions:
        print(f"  {r}")

    print("\nHOST watcher's commentary:")
    for line in host.lines:
        print(f"  {line}")

    print(f"\nSTATS watcher recorded {len(stats.records)} outcome(s):")
    for rec in stats.records:
        print(f"  payout_a={rec['payout_a']}  payout_b={rec['payout_b']}")

    print(f"\nSTATE watcher's final snapshot: last_event={state.current.get('last_event')}")


if __name__ == "__main__":
    asyncio.run(main())
