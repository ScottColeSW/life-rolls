"""Minimal local server for Life Rolls. Zero external dependencies --
mirrors Dominion's own prototype/server.py: stdlib only, so this runs
anywhere Python 3 runs.

Usage:
    python3 server.py
    then open http://localhost:8766 and click "Start Tournament".

The current game is a real Liar's Dice tournament (POST
/api/run-tournament -- see engine/liars_dice.py and design/DESIGN.md).
Computed synchronously server-side (including every live Ollama call)
and returned as one JSON blob; the browser replays the already-finished
result at a tuned pace. Real streaming (so a live model's "thinking" is
visible as it happens, not just replayed after the fact) is discussed in
the design doc but not yet built.

/api/run-case, /api/run-case-stream, and run_one_case below are the
superseded pot/split-steal game's endpoints -- kept because deleting
working code nobody asked to remove isn't the move, not because they're
part of the current game. legacy/ has that game's CLI scripts.
"""
from __future__ import annotations
import asyncio
import dataclasses
import json
import os
import random
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))

from engine.cases import CASE_LIBRARY
from engine.eventbus import EventBus
from engine.history import TournamentHistoryRecorder, get_stats, init_db
from engine.liars_dice import HAND_SIZE, Bid, run_tournament
from engine.live_case import run_case_on_bus, run_tournament_on_bus
from engine.negotiation import OllamaNegotiator, ScriptedNegotiator, TEXT_MODELS, run_case
from engine.personas import draw_cast
from engine.tournament_watchers import HighlightWatcher
from engine.watchers import AudienceWatcher, DiagramRelayWatcher, HostWatcher, StateWatcher, StatsWatcher

WEB_DIR = Path(__file__).parent / "web"
PORT = int(os.environ.get("LIFE_ROLLS_PORT", "8766"))
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
OLLAMA_TAGS_TIMEOUT = 3.0

# Human-readable display names for the TEXT_MODELS tags -- mirrors the
# sibling Dominion project's own roster panel (MODEL_DISPLAY_NAMES),
# since raw tags like "qwen2.5:3b" read as machine identifiers, not a
# roster a viewer recognizes at a glance.
MODEL_DISPLAY_NAMES = {
    "llama3.2:latest": "Llama 3.2",
    "qwen2.5:3b": "Qwen 2.5 (3B)",
    "gemma2:2b": "Gemma 2",
    "phi3:mini": "Phi-3 Mini",
    "qwen2.5:7b": "Qwen 2.5 (7B)",
}


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=2.0)
        return True
    except (urllib.error.URLError, OSError):
        return False


def get_model_roster() -> dict:
    """Cross-references TEXT_MODELS (the actual pool a live tournament
    draws from) against Ollama's own /api/tags -- one quick, short-timeout
    call, not per-model, since /api/tags always returns everything
    installed in one shot. Mirrors Dominion's get_model_roster() exactly.
    ollama_reachable is False, and every model reports online=False, if
    Ollama isn't running at all (connection refused) -- distinct from a
    model simply not being installed while Ollama itself is up. Backs the
    roster panel (Scott: "a player/model/online list badge on the side of
    the play board," replacing the single long status string that used to
    list every seat's model assignment as one run-on line)."""
    installed = set()
    reachable = False
    try:
        req = urllib.request.Request(OLLAMA_TAGS_URL)
        with urllib.request.urlopen(req, timeout=OLLAMA_TAGS_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        reachable = True
        for entry in data.get("models", []):
            name = entry.get("name") or entry.get("model")
            if name:
                installed.add(name)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        pass  # Ollama not running/unreachable -- roster still returns, all offline.

    models = [
        {
            "tag": tag,
            "display_name": MODEL_DISPLAY_NAMES.get(tag, tag),
            # A model only counts as genuinely online if Ollama itself is
            # reachable AND that specific model is actually installed --
            # either condition failing means a live tournament can't
            # actually call it (engine/negotiation.py's OllamaNegotiator
            # has no graceful scripted fallback for a dice-bidding turn --
            # see liars_dice.py's module docstring).
            "online": reachable and tag in installed,
        }
        for tag in TEXT_MODELS
    ]
    return {"ollama_reachable": reachable, "models": models}


def run_one_case(seed: "int | None", live_requested: bool) -> dict:
    rng = random.Random(seed)
    case = rng.choice(CASE_LIBRARY)
    persona_a, persona_b = draw_cast(2, rng)

    use_live = live_requested and _ollama_reachable()
    model_a = model_b = None
    if use_live:
        model_a, model_b = rng.sample(TEXT_MODELS, k=2)
        negotiator_a = OllamaNegotiator(rng, model_a)
        negotiator_b = OllamaNegotiator(rng, model_b)
    else:
        negotiator_a = ScriptedNegotiator(rng)
        negotiator_b = ScriptedNegotiator(rng)

    result = run_case(case, persona_a, persona_b, negotiator_a, negotiator_b,
                       debate_rounds=2 if use_live else 1)

    payload = dataclasses.asdict(result)
    payload["live"] = use_live
    payload["live_requested"] = live_requested
    payload["model_a"] = model_a
    payload["model_b"] = model_b
    return payload


def run_one_tournament(seed: "int | None", live_requested: bool, num_players: int) -> dict:
    """This project's actual game now (see design/DESIGN.md: "Life Rolls,
    not Split Decision") -- a real multi-round Liar's Dice tournament.
    run_case/run_one_case above are the superseded pot/split-steal
    mechanic, kept only because deleting working code nobody asked to
    remove isn't the move -- this is the live endpoint."""
    rng = random.Random(seed)
    case = rng.choice(CASE_LIBRARY)
    personas = draw_cast(num_players, rng)

    use_live = live_requested and _ollama_reachable()
    if use_live:
        models = [rng.choice(TEXT_MODELS) for _ in personas]
        negotiators = [OllamaNegotiator(rng, m) for m in models]
    else:
        models = [None] * len(personas)
        negotiators = [ScriptedNegotiator(rng) for _ in personas]

    result = run_tournament(personas, negotiators, case, rng)

    return {
        "case": dataclasses.asdict(case),
        "personas": [dataclasses.asdict(p) for p in personas],
        "models": models,
        "live": use_live,
        "live_requested": live_requested,
        "winner_index": result.winner_index,
        "order_of_elimination": result.order_of_elimination,
        "rounds": [dataclasses.asdict(r) for r in result.rounds],
    }


async def _run_streamed_tournament(seed: "int | None", live_requested: bool, num_players: int,
                                    sink: Callable[[Dict[str, Any]], None]) -> None:
    """Real streaming, unlike run_one_tournament above -- fixes the "Start
    Tournament seems to hang" report: with live Ollama, the whole
    precompute-then-replay approach means the browser sees nothing at all
    until every single round-trip across the entire tournament has
    already finished, which can genuinely take minutes with no feedback.
    This streams a 'preflight' status line per real setup step (model
    assignment, etc.) before anything else, then every raw tournament
    event AS it happens (via run_tournament_on_bus), plus HighlightWatcher's
    curated 'highlight' events for host-style commentary -- both flow
    through the same relay to the browser, since the relay is just
    subscribed to everything on the bus, including what HighlightWatcher
    itself publishes back onto it."""
    rng = random.Random(seed)
    case = rng.choice(CASE_LIBRARY)
    personas = draw_cast(num_players, rng)

    sink({"kind": "preflight", "text": "Drawing the table..."})
    use_live = live_requested and _ollama_reachable()
    if use_live:
        models = [rng.choice(TEXT_MODELS) for _ in personas]
        negotiators = [OllamaNegotiator(rng, m) for m in models]
        sink({"kind": "preflight", "text": "Ollama reachable -- assigning live models:"})
        for p, m in zip(personas, models):
            sink({"kind": "preflight", "text": f"  {p.archetype_name} -> {m}"})
    else:
        models = [None] * len(personas)
        negotiators = [ScriptedNegotiator(rng) for _ in personas]
        sink({"kind": "preflight", "text": "Ollama not reachable -- falling back to scripted players."
              if live_requested else "Scripted players -- instant moves."})

    sink({"kind": "table_ready", "case": dataclasses.asdict(case),
          "personas": [dataclasses.asdict(p) for p in personas], "models": models, "live": use_live,
          "live_requested": live_requested, "hand_size": HAND_SIZE})

    loop = asyncio.get_event_loop()
    bus = EventBus(loop)
    highlight = HighlightWatcher(bus, personas)
    watchers = [highlight]
    queues = [bus.subscribe() for _ in watchers]
    tasks = [asyncio.create_task(w.run(q)) for w, q in zip(watchers, queues)]

    def _json_safe(data: Dict[str, Any]) -> Dict[str, Any]:
        return {k: (dataclasses.asdict(v) if isinstance(v, Bid) else v) for k, v in data.items()}

    # Recorded to SQLite (engine/history.py) alongside streaming to the
    # browser -- every tournament gets captured automatically, not just
    # the one currently being watched, so patterns (which model tends to
    # win, whether Spot On/Ace-Switch actually pay off) can be analyzed
    # across many tournaments later. Fed the raw (kind, data) straight off
    # the bus, same as the browser relay right below it.
    recorder = TournamentHistoryRecorder(personas, models, live=use_live, seed=seed)

    relay_queue = bus.subscribe()

    async def relay_loop() -> None:
        while True:
            event = await relay_queue.get()
            recorder.on_event(event.kind, event.data)
            sink({"kind": event.kind, **_json_safe(event.data)})
            if event.kind == "tournament_winner":
                break
    relay_task = asyncio.create_task(relay_loop())

    await run_tournament_on_bus(personas, negotiators, case, rng, bus)
    await asyncio.gather(*tasks, relay_task)


async def _run_streamed_case(seed: "int | None", live_requested: bool,
                              sink: Callable[[Dict[str, Any]], None]) -> None:
    """Runs one case through the real EventBus (engine/eventbus.py) with
    all four watchers subscribed, PLUS a DiagramRelayWatcher whose sink is
    this function's own `sink` callback -- which server.py wires to write
    one NDJSON line per event straight to the connected browser's live
    diagram (web/diagram.html). This is the same bus, the same four
    watchers, as pubsub_demo.py -- the diagram is a fifth subscriber, not
    a separate mechanism pretending to show one."""
    rng = random.Random(seed)
    case = rng.choice(CASE_LIBRARY)
    persona_a, persona_b = draw_cast(2, rng)

    use_live = live_requested and _ollama_reachable()
    if use_live:
        model_a, model_b = rng.sample(TEXT_MODELS, k=2)
        negotiator_a, negotiator_b = OllamaNegotiator(rng, model_a), OllamaNegotiator(rng, model_b)
    else:
        negotiator_a, negotiator_b = ScriptedNegotiator(rng), ScriptedNegotiator(rng)

    loop = asyncio.get_event_loop()
    bus = EventBus(loop)
    watchers = [AudienceWatcher(), HostWatcher(), StatsWatcher(), StateWatcher(),
                DiagramRelayWatcher(sink)]
    # Subscribe every queue synchronously, before the case starts publishing
    # -- see engine/watchers.py's _drain docstring for why this ordering
    # matters and can't be left to each watcher's own coroutine.
    queues = [bus.subscribe() for _ in watchers]
    tasks = [asyncio.create_task(w.run(q)) for w, q in zip(watchers, queues)]

    sink({"kind": "meta", "label": f"live={use_live}", "targets": []})
    await run_case_on_bus(case, persona_a, persona_b, negotiator_a, negotiator_b,
                           bus, debate_rounds=2 if use_live else 1)
    await asyncio.gather(*tasks)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/stats":
            # Aggregated across every tournament ever recorded to
            # engine/life_rolls_history.db, not just whatever's currently
            # on screen -- backs the Standings page (web/stats.html).
            body = json.dumps(get_stats()).encode("utf-8")
            self._send(200, body, "application/json")
            return
        if path == "/api/models":
            # Backs the roster panel -- see get_model_roster above. Polled
            # periodically by the frontend, not just once, since whether
            # Ollama is up/down or a model is installed can change while
            # the page stays open.
            body = json.dumps(get_model_roster()).encode("utf-8")
            self._send(200, body, "application/json")
            return
        if path == "/":
            path = "/index.html"
        file_path = (WEB_DIR / path.lstrip("/")).resolve()
        if WEB_DIR not in file_path.parents and file_path != WEB_DIR:
            self._send(403, b"forbidden", "text/plain")
            return
        if not file_path.exists():
            self._send(404, b"not found", "text/plain")
            return
        content_type = "text/html; charset=utf-8"
        if file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        self._send(200, file_path.read_bytes(), content_type)

    def do_POST(self) -> None:
        if self.path.startswith("/api/run-tournament-stream"):
            # Checked BEFORE the plain /api/run-tournament prefix below --
            # same reason as run-case-stream vs run-case: the more
            # specific route has to win first or it's unreachable.
            qs = parse_qs(urlparse(self.path).query)
            seed = int(qs["seed"][0]) if "seed" in qs else None
            live_requested = qs.get("live", ["0"])[0] == "1"
            num_players = int(qs.get("players", ["5"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def sink(payload: Dict[str, Any]) -> None:
                self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")
                self.wfile.flush()

            try:
                asyncio.run(_run_streamed_tournament(seed, live_requested, num_players, sink))
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass  # browser tab closed mid-stream -- nothing to recover
            return
        if self.path.startswith("/api/run-tournament"):
            qs = parse_qs(urlparse(self.path).query)
            seed = int(qs["seed"][0]) if "seed" in qs else None
            live_requested = qs.get("live", ["0"])[0] == "1"
            num_players = int(qs.get("players", ["5"])[0])
            try:
                payload = run_one_tournament(seed, live_requested, num_players)
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as e:  # noqa: BLE001 -- surfaced to the UI, not swallowed
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self._send(500, body, "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/run-case-stream"):
            # Checked BEFORE the plain /api/run-case prefix below --
            # "/api/run-case-stream" also starts with "/api/run-case", so
            # the more specific route has to win first or this branch is
            # unreachable.
            qs = parse_qs(urlparse(self.path).query)
            seed = int(qs["seed"][0]) if "seed" in qs else None
            live_requested = qs.get("live", ["0"])[0] == "1"
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def sink(payload: Dict[str, Any]) -> None:
                self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")
                self.wfile.flush()

            try:
                asyncio.run(_run_streamed_case(seed, live_requested, sink))
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass  # browser tab closed mid-stream -- nothing to recover, same as Dominion's own /api/run-show
            return
        if self.path.startswith("/api/run-case"):
            qs = parse_qs(urlparse(self.path).query)
            seed = int(qs["seed"][0]) if "seed" in qs else None
            live_requested = qs.get("live", ["0"])[0] == "1"
            try:
                payload = run_one_case(seed, live_requested)
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as e:  # noqa: BLE001 -- surfaced to the UI, not swallowed
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self._send(500, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, format: str, *args) -> None:
        pass  # keep the console quiet; comment out to debug


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"Life Rolls running at http://localhost:{PORT}")
    server.serve_forever()
