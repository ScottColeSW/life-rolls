"""Async pub/sub event bus.

One publisher -- the negotiation engine -- and any number of independent
subscribers: the audience, the host, stats, live state, and whatever gets
added after those. publish() never needs to know who, if anyone, is
listening, and the engine that calls it never imports or references a
single watcher directly (see engine/watchers.py). Adding a new watcher
later is exactly one more subscribe() call -- this file never changes to
support it.

The negotiation engine itself (engine/negotiation.py's run_case) is still
synchronous -- it makes real blocking HTTP calls to Ollama, and rewriting
that to true async I/O is a separate, bigger piece of work, not something
to bundle into this. Instead, run_case runs in a worker thread (see
engine/live_case.py) and publishes into this bus via call_soon_threadsafe,
which is the one genuinely thread-safety-sensitive detail here: asyncio
queues are only safe to push into from the loop's own thread, so publish()
always schedules the actual put through the loop rather than touching the
queue directly from whatever thread called it.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Event:
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        # Captured at construction time, not lazily -- publish() may be
        # called from a worker thread that has no event loop of its own to
        # fall back on.
        self._loop = loop or asyncio.get_event_loop()
        self._subscribers: List["asyncio.Queue[Event]"] = []

    def subscribe(self) -> "asyncio.Queue[Event]":
        queue: "asyncio.Queue[Event]" = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Event]") -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def publish(self, kind: str, **data: Any) -> None:
        """Safe to call from the event loop's own thread OR from a
        worker thread running the synchronous negotiation engine."""
        event = Event(kind=kind, data=data)
        for queue in list(self._subscribers):
            self._loop.call_soon_threadsafe(queue.put_nowait, event)
