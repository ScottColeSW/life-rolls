"""Watchers for the Liar's Dice tournament (engine/liars_dice.py) --
sibling to engine/watchers.py's older case-based watchers (that game is
superseded now, see design/DESIGN.md's "Life Rolls, not Split Decision").
Same pattern: independent subscribers, never known to the engine itself,
so adding one more is always just one more class in this shape.

HighlightWatcher is the new piece: Scott's "a model output filter,
limiting emissions to interesting choices or decision chains, not just a
stream of consciousness." It doesn't filter raw model text -- it doesn't
need to, since everything the engine does is already a structured event,
not free text. It curates the STREAM OF EVENTS instead: round_call,
round_execution, player_eliminated, and tournament_winner are each
inherently rare, climactic beats (at most one per round), so they're
always surfaced. round_raise is the noisy one -- most raises in a long
bidding chain are routine, so only a round's opening bid and a notably
bold jump get surfaced as highlights; every other raise still reaches
TournamentStatsWatcher for the "measure it all" data views, it just
doesn't interrupt the live commentary.

TournamentHostWatcher is the concrete version of "the Host should showrun,
not just react to the same raw events everyone else sees" -- it subscribes
to HighlightWatcher's own published output, not the raw firehose.
HighlightWatcher publishing back onto the SAME bus it subscribes to is
what makes this possible without Host needing its own copy of the
filtering logic, or the engine needing to know either watcher exists.
"""
from __future__ import annotations
import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .eventbus import Event, EventBus
from .liars_dice import Bid
from .personas import Persona


async def _drain(queue: "asyncio.Queue[Event]", handle: Callable[[Event], Awaitable[None]],
                  stop_kind: str = "tournament_winner") -> None:
    """Same shared-driver-loop shape as engine/watchers.py's _drain, with
    the tournament's own sentinel (tournament_winner, not case_end) --
    kept local rather than imported since the two games' event vocabularies
    are otherwise unrelated and importing across them would just be
    confusing, not real code reuse."""
    while True:
        event = await queue.get()
        await handle(event)
        if event.kind == stop_kind:
            break


class TournamentStateWatcher:
    """The current live snapshot -- same purpose as the old game's
    StateWatcher: 'what beat are we on, right now.'"""

    def __init__(self) -> None:
        self.current: Dict[str, Any] = {}

    async def _handle(self, event: Event) -> None:
        self.current = {"last_event": event.kind, **event.data}

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)


class TournamentStatsWatcher:
    """Records every climactic beat across the whole tournament -- the
    'measure it all' data views: every call, execution, elimination, and
    the eventual winner. In-memory for v0; real persistence is a change
    to this one class alone, same as the old game's StatsWatcher."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    async def _handle(self, event: Event) -> None:
        if event.kind in ("round_call", "round_execution", "player_eliminated", "tournament_winner"):
            self.records.append({"kind": event.kind, **event.data})

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)


class HighlightWatcher:
    """See module docstring. Needs the bus itself (to publish its curated
    'highlight' events back onto it) and the persona list (to name names
    in its text) at construction."""

    # A real quantity leap (skipping past the very next value) is what
    # actually reads as bold to a viewer -- NOT Bid.value()'s encoding,
    # which was designed purely for strict-ordering validation, not for
    # "how big does this look" semantics. A first cut of this filter
    # compared value() directly and it barely filtered anything (71 of 78
    # raw raises got flagged in testing): quantity is weighted by 7 in
    # that encoding, so even the most routine "same face, quantity+1"
    # raise already clears a value-delta of 3. Comparing the actual
    # quantity delta instead means a routine same-quantity face bump (the
    # common case from compute_new_face_raise, which only touches
    # quantity when forced to) correctly stays quiet.
    BIG_QUANTITY_JUMP = 2

    def __init__(self, bus: EventBus, personas: List[Persona]) -> None:
        self.bus = bus
        self.personas = personas
        self.highlights: List[str] = []
        self._last_bid: Optional[Bid] = None

    def _name(self, idx: Optional[int]) -> str:
        return self.personas[idx].archetype_name if idx is not None else "someone"

    def _publish(self, text: str, level: str) -> None:
        self.highlights.append(text)
        self.bus.publish("highlight", text=text, level=level)

    async def _handle(self, event: Event) -> None:
        if event.kind == "round_start":
            self._last_bid = None
        elif event.kind == "round_raise":
            bid: Bid = event.data["bid"]
            is_opening = self._last_bid is None
            is_big_jump = (not is_opening) and (bid.quantity - self._last_bid.quantity >= self.BIG_QUANTITY_JUMP)
            if is_opening:
                self._publish(f"{self._name(event.data['player_index'])} opens: at least "
                              f"{bid.quantity} showing {bid.face}.", level="notable")
            elif is_big_jump:
                self._publish(f"{self._name(event.data['player_index'])} jumps boldly to at least "
                              f"{bid.quantity} showing {bid.face}.", level="notable")
            self._last_bid = bid
        elif event.kind == "round_call":
            claimant, caller = self._name(event.data["claimant_index"]), self._name(event.data["caller_index"])
            verdict = "held up" if event.data["bid_was_true"] else "was a bluff"
            self._publish(f"{caller} calls {claimant}'s bid -- it {verdict} "
                          f"(actual count {event.data['actual_count']}).", level="major")
        elif event.kind == "round_execution":
            self._publish(f"{self._name(event.data['player_index'])} froze and was executed on the spot.",
                          level="major")
        elif event.kind == "player_eliminated":
            self._publish(f"{self._name(event.data['player_index'])} is eliminated.", level="major")
        elif event.kind == "tournament_winner":
            self._publish(f"{self._name(event.data['player_index'])} wins the whole pot!", level="climax")

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)


class TournamentHostWatcher:
    """The showrunner. Subscribes to HighlightWatcher's curated
    'highlight' events specifically -- not the raw round_raise firehose --
    which is the actual point: Host's commentary is only ever as noisy as
    HighlightWatcher decided the show should be, not as noisy as the
    engine actually is underneath. Scripted/templated for v0, same
    reasoning as everywhere else: always available, a live model voice is
    a later upgrade, not a day-one dependency."""

    def __init__(self) -> None:
        self.lines: List[str] = []

    async def _handle(self, event: Event) -> None:
        if event.kind != "highlight":
            return
        prefix = {"notable": "", "major": "Big moment -- ", "climax": "THAT'S IT -- "}[event.data["level"]]
        self.lines.append(prefix + event.data["text"])

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)
