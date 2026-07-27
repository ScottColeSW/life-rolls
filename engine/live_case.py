"""Bridges the synchronous negotiation engine into the async EventBus.

run_case (engine/negotiation.py) is still a plain blocking function -- it
makes real synchronous HTTP calls to Ollama, and rewriting that to true
async I/O is separate work, not bundled in here. Instead, it runs in a
worker thread via asyncio.to_thread, and its existing on_event callback
just calls bus.publish(), which is already safe to call from any thread
(see engine/eventbus.py). Every watcher downstream never knows or cares
that the engine itself isn't "really" async -- they only see events
arriving on their own queue.
"""
from __future__ import annotations
import asyncio
import dataclasses
from typing import Optional

from typing import Any, List

from .cases import Case
from .eventbus import EventBus
from .liars_dice import TournamentResult, run_tournament
from .negotiation import NegotiationResult, ScriptedNegotiator, run_case
from .personas import Persona


async def run_case_on_bus(case: Case, persona_a: Persona, persona_b: Persona,
                           negotiator_a: ScriptedNegotiator, negotiator_b: ScriptedNegotiator,
                           bus: EventBus, debate_rounds: int = 1) -> NegotiationResult:
    def on_event(kind: str, data: dict) -> None:
        bus.publish(kind, **data)

    result: NegotiationResult = await asyncio.to_thread(
        run_case, case, persona_a, persona_b, negotiator_a, negotiator_b,
        on_event=on_event, debate_rounds=debate_rounds,
    )
    # Sentinel every watcher's _drain loop watches for -- lets each one
    # know this case is over and it's safe to stop consuming, without the
    # engine needing to know watchers exist at all.
    bus.publish("case_end", result=dataclasses.asdict(result))
    return result


async def run_tournament_on_bus(personas: List[Persona], negotiators: List[Any], case: Case,
                                 rng, bus: EventBus) -> TournamentResult:
    """Same bridge pattern as run_case_on_bus, for the actual current game
    (engine/liars_dice.py). run_tournament already emits its own natural
    sentinel (tournament_winner) as its last event, so no artificial one
    is needed here the way case_end was for the older game."""
    def on_event(kind: str, data: dict) -> None:
        bus.publish(kind, **data)

    return await asyncio.to_thread(run_tournament, personas, negotiators, case, rng, on_event=on_event)
