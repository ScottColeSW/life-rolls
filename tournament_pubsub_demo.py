"""Proves the HighlightWatcher actually curates the stream, and Host
reacts only to curated highlights, not the raw firehose.

    python tournament_pubsub_demo.py
    python tournament_pubsub_demo.py --live
"""
from __future__ import annotations
import argparse
import asyncio
import random
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.cases import CASE_LIBRARY
from engine.eventbus import EventBus
from engine.live_case import run_tournament_on_bus
from engine.negotiation import OllamaNegotiator, ScriptedNegotiator, TEXT_MODELS
from engine.personas import draw_cast
from engine.tournament_watchers import (
    HighlightWatcher, TournamentHostWatcher, TournamentStateWatcher, TournamentStatsWatcher,
)


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError):
        return False


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--players", type=int, default=5)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    case = rng.choice(CASE_LIBRARY)
    personas = draw_cast(args.players, rng)

    use_live = args.live and _ollama_reachable()
    if use_live:
        negotiators = [OllamaNegotiator(rng, rng.choice(TEXT_MODELS)) for _ in personas]
    else:
        negotiators = [ScriptedNegotiator(rng) for _ in personas]

    loop = asyncio.get_event_loop()
    bus = EventBus(loop)

    state, stats, host = TournamentStateWatcher(), TournamentStatsWatcher(), TournamentHostWatcher()
    highlight = HighlightWatcher(bus, personas)  # constructed with the bus so it can publish back onto it
    watchers = [state, stats, host, highlight]
    queues = [bus.subscribe() for _ in watchers]
    tasks = [asyncio.create_task(w.run(q)) for w, q in zip(watchers, queues)]

    # Count every raw round_raise separately, purely to prove the
    # filtering ratio -- not a watcher, just a plain counter subscribed
    # the same way everything else is.
    raw_raise_queue = bus.subscribe()
    raw_raise_count = 0

    async def count_raises():
        nonlocal raw_raise_count
        while True:
            event = await raw_raise_queue.get()
            if event.kind == "round_raise":
                raw_raise_count += 1
            if event.kind == "tournament_winner":
                break
    counter_task = asyncio.create_task(count_raises())

    print(f"Case: {case.name}  --  {len(personas)} players: " + ", ".join(p.archetype_name for p in personas))
    await run_tournament_on_bus(personas, negotiators, case, rng, bus)
    await asyncio.gather(*tasks, counter_task)

    print(f"\nRaw round_raise events: {raw_raise_count}")
    print(f"HighlightWatcher surfaced: {len(highlight.highlights)} highlights")
    print(f"TournamentHostWatcher announced: {len(host.lines)} lines\n")

    print("--- Host's actual commentary (curated, not the raw stream) ---")
    for line in host.lines:
        print(f"  {line}")

    print(f"\nTournamentStatsWatcher recorded {len(stats.records)} climactic events total "
          f"(calls + executions + eliminations + winner)")
    print(f"Final state snapshot: last_event={state.current.get('last_event')}")


if __name__ == "__main__":
    asyncio.run(main())
