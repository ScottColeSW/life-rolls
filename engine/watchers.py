"""Watchers: independent subscribers to the negotiation EventBus.

None of these are players, and none of them are known to
engine/negotiation.py -- they only ever observe events the engine already
publishes. Adding a new watcher later (a highlight-reel detector, a
notification hook, whatever comes up) means writing one more class in
this shape and subscribing it -- nothing in the engine itself ever needs
to change to support it. That's the actual point of the bus.

All four below are scripted/deterministic for v0 -- same "always
available, never stalls the show" reasoning as ScriptedNegotiator: a live
model-generated crowd reaction or host voice is a natural upgrade later,
not a day-one dependency.
"""
from __future__ import annotations
import asyncio
from typing import Any, Awaitable, Callable, Dict, List

from .eventbus import Event

# Events any in-fiction "audience" is allowed to react to -- public
# information only. gut_read and real_intent are private thought bubbles
# (see design/DESIGN.md's Transparency section): only the human running
# the app sees those, same as a real audience never hears a contestant's
# inner monologue. This is what keeps the "everyone is an agent, humans
# play god" framing coherent -- the in-show crowd is just another agent
# with limited information, distinct from the human's full-transparency view.
PUBLIC_EVENTS = {"case_reveal", "debate_line", "reveal"}


async def _drain(queue: "asyncio.Queue[Event]",
                  handle: Callable[[Event], Awaitable[None]]) -> None:
    """Shared driver loop every watcher below uses: consume events on an
    ALREADY-subscribed queue until the case_end sentinel, dispatching each
    to handle(). Subscribing happens in the caller, synchronously, before
    the case is started (see engine/live_case.py / the demo runner) --
    never inside this coroutine. A watcher that subscribed lazily, on its
    own schedule, would race the case's first publish() with no guarantee
    it's registered in time; subscribing up front closes that race
    entirely instead of hoping the scheduler favors the watcher."""
    try:
        while True:
            event = await queue.get()
            await handle(event)
            if event.kind == "case_end":
                break
    finally:
        pass  # unsubscribing is the caller's responsibility -- it holds the queue/bus pairing


class AudienceWatcher:
    """The in-show crowd. Reacts to public beats only -- never the private
    thought bubbles, never the hidden resolve numbers once those exist."""

    def __init__(self) -> None:
        self.reactions: List[str] = []

    async def _handle(self, event: Event) -> None:
        if event.kind not in PUBLIC_EVENTS:
            return
        if event.kind == "case_reveal":
            self.reactions.append("(the crowd leans in)")
        elif event.kind == "debate_line":
            self.reactions.append("(scattered murmurs)")
        elif event.kind == "reveal":
            both_split = (event.data["decision_a"] == "split"
                          and event.data["decision_b"] == "split")
            self.reactions.append("(a relieved cheer)" if both_split
                                   else "(the crowd erupts)")

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)


class HostWatcher:
    """Commentary keyed to the case's game-theory framework -- the "host
    calls out the strategy" idea from design/DESIGN.md, so an obvious
    Prisoner's-Dilemma read isn't the only shape viewers ever hear named."""

    FRAMEWORK_LINES: Dict[str, str] = {
        "Ultimatum": "Careful -- Ultimatum Games punish a lowball offer. History says spite wins here.",
        "Trust": "Someone has to move first in a Trust Game -- and whoever does is genuinely exposed.",
        "Stag Hunt": "Classic Stag Hunt: the payoff's best if both commit, but it's always safer to defect.",
        "Chicken": "This is a standoff. In Chicken, neither backing down is worse for both than either one folding.",
    }

    def __init__(self) -> None:
        self.lines: List[str] = []

    async def _handle(self, event: Event) -> None:
        if event.kind == "case_reveal":
            case = event.data["case"]
            self.lines.append(self.FRAMEWORK_LINES.get(
                case.framework, f"Tonight's case is a {case.framework} at heart."))
        elif event.kind == "reveal":
            both_split = (event.data["decision_a"] == "split"
                          and event.data["decision_b"] == "split")
            self.lines.append("They split. Cooperation held." if both_split
                               else "Someone stole. The trust didn't survive contact.")

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)


class StatsWatcher:
    """Persists outcomes across every case, every show -- the "measure it
    all" leaderboard from design/DESIGN.md. In-memory for v0; swapping
    this for real SQLite persistence (Dominion's history.py pattern) is a
    change to this one class alone."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    async def _handle(self, event: Event) -> None:
        if event.kind == "reveal":
            self.records.append(dict(event.data))

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)


class StateWatcher:
    """The current live snapshot of whatever case is running right now --
    distinct from StatsWatcher's history-across-every-case: this answers
    'what beat are we on, right now,' for a client that connects or
    refreshes mid-case instead of having watched it from the start."""

    def __init__(self) -> None:
        self.current: Dict[str, Any] = {}

    async def _handle(self, event: Event) -> None:
        self.current = {"last_event": event.kind, **event.data}

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)


class DiagramRelayWatcher:
    """Turns each event into a small, JSON-safe summary and hands it to an
    external sink -- e.g. server.py, writing one NDJSON line per event to
    a connected browser's live system diagram (web/diagram.html). This is
    the actual proof that adding a new watcher really is just one more
    class in this shape: nothing in the engine, or in AudienceWatcher /
    HostWatcher / StatsWatcher / StateWatcher, changes to support this.

    TARGETS mirrors the other three watchers' own _handle logic (which
    event kinds each one actually reacts to) purely for the diagram's
    benefit -- it's a manually-kept summary of behavior that already lives
    in this file, not a new source of truth. If one of those _handle
    methods ever changes which kinds it reacts to, this table needs a
    matching update, or the diagram will show a pulse reaching a watcher
    that didn't actually do anything that turn."""

    TARGETS: Dict[str, List[str]] = {
        "case_reveal": ["audience", "host", "state"],
        "gut_read": ["state"],
        "debate_line": ["audience", "state"],
        "real_intent": ["state"],
        "reveal": ["audience", "host", "stats", "state"],
        "case_end": ["state"],
    }

    def __init__(self, sink: Callable[[Dict[str, Any]], None]) -> None:
        self.sink = sink

    def _label(self, event: Event) -> str:
        kind, data = event.kind, event.data
        if kind == "case_reveal":
            case = data["case"]
            return f"CASE: {case.name} ({case.framework})"
        if kind == "gut_read":
            return f"{data['persona'].archetype_name}: gut read"
        if kind == "debate_line":
            text = data["text"]
            short = text if len(text) <= 46 else text[:46] + "..."
            return f'{data["persona"].archetype_name}: "{short}"'
        if kind == "real_intent":
            return f"{data['persona'].archetype_name}: final thought"
        if kind == "reveal":
            return f"REVEAL: {data['decision_a'].upper()} / {data['decision_b'].upper()}"
        if kind == "case_end":
            return "case complete"
        return kind

    async def _handle(self, event: Event) -> None:
        self.sink({
            "kind": event.kind,
            "label": self._label(event),
            "targets": self.TARGETS.get(event.kind, []),
        })

    async def run(self, queue: "asyncio.Queue[Event]") -> None:
        await _drain(queue, self._handle)
