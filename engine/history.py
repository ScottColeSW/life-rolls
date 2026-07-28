"""Long-term SQLite capture of every Liar's Dice tournament played, so
patterns (which persona/model tends to win, how often Spot On and
Ace-Switch actually get reached for, how they pay off) can be analyzed
across many tournaments instead of just whichever one is currently on
screen. Mirrors the sibling Dominion project's own engine/history.py --
same shape: TournamentHistoryRecorder.on_event() is fed the exact same
(kind, data) stream server.py already relays to the browser, right
alongside it. run_tournament/run_round have no idea this exists, matching
this project's "engine emits events, consumers subscribe" design (see
engine/tournament_watchers.py).

Query examples once you've got a few tournaments recorded:

    -- win rate by model
    SELECT model, COUNT(*) FILTER (WHERE is_winner) * 1.0 / COUNT(*) AS win_rate
    FROM player_stats WHERE model IS NOT NULL GROUP BY model;

    -- does Spot On actually pay off, or is it just a flashy bad bet?
    SELECT SUM(spot_ons_correct) * 1.0 / SUM(spot_ons_attempted) AS hit_rate
    FROM player_stats WHERE spot_ons_attempted > 0;
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "life_rolls_history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    live INTEGER NOT NULL,
    num_players INTEGER NOT NULL,
    total_rounds INTEGER,
    winner_index INTEGER,
    winner_archetype TEXT,
    winner_model TEXT
);

CREATE TABLE IF NOT EXISTS player_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
    player_index INTEGER NOT NULL,
    archetype_name TEXT,
    model TEXT,
    risk_tolerance REAL,
    trust_propensity REAL,
    is_winner INTEGER NOT NULL,
    calls_made INTEGER NOT NULL DEFAULT 0,
    calls_correct INTEGER NOT NULL DEFAULT 0,
    spot_ons_attempted INTEGER NOT NULL DEFAULT 0,
    spot_ons_correct INTEGER NOT NULL DEFAULT 0,
    raises_made INTEGER NOT NULL DEFAULT 0,
    ace_switches_made INTEGER NOT NULL DEFAULT 0,
    dice_lost INTEGER NOT NULL DEFAULT 0,
    dice_gained INTEGER NOT NULL DEFAULT 0,
    executions INTEGER NOT NULL DEFAULT 0,
    eliminated_round INTEGER
);
"""


def init_db(db_path: str = DB_PATH) -> None:
    """Idempotent; call once at server startup."""
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TournamentHistoryRecorder:
    """Fed the exact (kind, data) stream server.py relays to the browser
    via on_event(kind, data) -- the same calling convention
    engine/liars_dice.py's own on_event callback already uses, not the
    async EventBus Event object shape tournament_watchers.py's watchers
    consume (this recorder is a plain synchronous object, chained directly
    into server.py's relay loop the same way Dominion's HistoryRecorder is
    chained into its write_event callback -- no separate bus subscription
    needed). on_event never raises -- a storage hiccup (locked/corrupt db
    file, disk full) should never be able to interrupt or crash a live
    tournament over what's ultimately a nice-to-have."""

    def __init__(self, personas: List[Any], models: List[Optional[str]],
                 live: bool, seed: Optional[int], db_path: str = DB_PATH):
        self.live = live
        self.seed = seed
        self.db_path = db_path
        self.started_at = _utcnow()
        self.round_count = 0
        self.players: Dict[int, Dict[str, Any]] = {
            i: {
                "archetype_name": p.archetype_name, "model": models[i] if i < len(models) else None,
                "risk_tolerance": p.risk_tolerance, "trust_propensity": p.trust_propensity,
                "calls_made": 0, "calls_correct": 0, "spot_ons_attempted": 0,
                "spot_ons_correct": 0, "raises_made": 0, "ace_switches_made": 0,
                "dice_lost": 0, "dice_gained": 0, "executions": 0, "eliminated_round": None,
            }
            for i, p in enumerate(personas)
        }
        # Tracks the standing bid's face across a round so a raise that
        # crosses the ace/non-ace boundary can be counted as an
        # Ace-Switch -- the same detection HighlightWatcher does for
        # narration, independently duplicated here rather than shared
        # since the two watchers are deliberately independent (see
        # tournament_watchers.py's module docstring).
        self._last_bid_face: Optional[int] = None
        self._is_palafox_round = False

    def on_event(self, kind: str, data: Dict[str, Any]) -> None:
        try:
            self._handle(kind, data)
        except Exception:
            pass

    def _handle(self, kind: str, data: Dict[str, Any]) -> None:
        if kind == "round_start":
            self.round_count += 1
            self._last_bid_face = None
            self._is_palafox_round = bool(data.get("is_palafox"))
        elif kind == "round_raise":
            p = self.players.get(data["player_index"])
            if p is not None:
                p["raises_made"] += 1
                bid = data["bid"]
                crosses_ace_boundary = (
                    self._last_bid_face is not None and self._last_bid_face != bid.face
                    and (self._last_bid_face == 1 or bid.face == 1)
                )
                if crosses_ace_boundary and not self._is_palafox_round and not data.get("is_palafox_open"):
                    p["ace_switches_made"] += 1
                self._last_bid_face = bid.face
        elif kind == "round_call":
            caller = self.players.get(data["caller_index"])
            if caller is not None:
                caller["calls_made"] += 1
                # bid_was_true False means the claimant was bluffing, so
                # the caller's call was the CORRECT read.
                if not data["bid_was_true"]:
                    caller["calls_correct"] += 1
            loser_idx = data["caller_index"] if data["bid_was_true"] else data["claimant_index"]
            loser = self.players.get(loser_idx)
            if loser is not None:
                loser["dice_lost"] += 1
        elif kind == "round_spot_on":
            caller = self.players.get(data["caller_index"])
            if caller is not None:
                caller["spot_ons_attempted"] += 1
            if data["spot_on_correct"]:
                if caller is not None:
                    caller["spot_ons_correct"] += 1
                claimant = self.players.get(data["claimant_index"])
                if claimant is not None:
                    claimant["dice_lost"] += 1
                # The caller's gain is handled by the separate
                # player_gained_die event, not here.
            else:
                if caller is not None:
                    caller["dice_lost"] += 1
        elif kind == "player_gained_die":
            p = self.players.get(data["player_index"])
            if p is not None:
                p["dice_gained"] += 1
        elif kind == "round_execution":
            p = self.players.get(data["player_index"])
            if p is not None:
                p["executions"] += 1
                p["dice_lost"] += 1
        elif kind == "player_eliminated":
            p = self.players.get(data["player_index"])
            if p is not None and p["eliminated_round"] is None:
                p["eliminated_round"] = self.round_count
        elif kind == "tournament_winner":
            self._write(data["player_index"])

    def _write(self, winner_index: int) -> None:
        winner = self.players.get(winner_index, {})
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            cur = conn.execute(
                "INSERT INTO tournaments (seed, started_at, finished_at, live, num_players, "
                "total_rounds, winner_index, winner_archetype, winner_model) VALUES (?,?,?,?,?,?,?,?,?)",
                (self.seed, self.started_at, _utcnow(), int(self.live), len(self.players),
                 self.round_count, winner_index, winner.get("archetype_name"), winner.get("model")),
            )
            tournament_id = cur.lastrowid
            for idx, p in self.players.items():
                conn.execute(
                    "INSERT INTO player_stats (tournament_id, player_index, archetype_name, model, "
                    "risk_tolerance, trust_propensity, is_winner, calls_made, calls_correct, "
                    "spot_ons_attempted, spot_ons_correct, raises_made, ace_switches_made, "
                    "dice_lost, dice_gained, executions, eliminated_round) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tournament_id, idx, p["archetype_name"], p["model"], p["risk_tolerance"],
                     p["trust_propensity"], int(idx == winner_index), p["calls_made"],
                     p["calls_correct"], p["spot_ons_attempted"], p["spot_ons_correct"],
                     p["raises_made"], p["ace_switches_made"], p["dice_lost"], p["dice_gained"],
                     p["executions"], p["eliminated_round"]),
                )
            conn.commit()
        finally:
            conn.close()


def get_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Aggregate per-model stats across every recorded tournament, plus a
    recent-tournaments list -- backs the Standings page (web/stats.html).
    Aggregated by MODEL, not player_index or archetype -- those are
    redrawn fresh every tournament, so the only identity that actually
    persists across tournaments is which Ollama model was playing (or
    None for a scripted stand-in, excluded from the model leaderboard the
    same way Dominion's get_stats() excludes a null model)."""
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        tournaments_recorded = conn.execute("SELECT COUNT(*) AS n FROM tournaments").fetchone()["n"]
        rounds_recorded = conn.execute(
            "SELECT COALESCE(SUM(total_rounds), 0) AS n FROM tournaments"
        ).fetchone()["n"]

        models: Dict[str, Dict[str, Any]] = {}

        def ensure(model: str) -> Dict[str, Any]:
            return models.setdefault(model, {
                "model": model, "tournaments_played": 0, "wins": 0,
                "calls_made": 0, "calls_correct": 0, "spot_ons_attempted": 0,
                "spot_ons_correct": 0, "raises_made": 0, "ace_switches_made": 0,
                "dice_lost": 0, "dice_gained": 0, "executions": 0,
            })

        for row in conn.execute(
            "SELECT model, COUNT(*) AS n, SUM(is_winner) AS wins, SUM(calls_made) AS calls_made, "
            "SUM(calls_correct) AS calls_correct, SUM(spot_ons_attempted) AS spot_ons_attempted, "
            "SUM(spot_ons_correct) AS spot_ons_correct, SUM(raises_made) AS raises_made, "
            "SUM(ace_switches_made) AS ace_switches_made, SUM(dice_lost) AS dice_lost, "
            "SUM(dice_gained) AS dice_gained, SUM(executions) AS executions "
            "FROM player_stats WHERE model IS NOT NULL GROUP BY model"
        ):
            m = ensure(row["model"])
            m.update({
                "tournaments_played": row["n"], "wins": row["wins"] or 0,
                "calls_made": row["calls_made"] or 0, "calls_correct": row["calls_correct"] or 0,
                "spot_ons_attempted": row["spot_ons_attempted"] or 0,
                "spot_ons_correct": row["spot_ons_correct"] or 0,
                "raises_made": row["raises_made"] or 0,
                "ace_switches_made": row["ace_switches_made"] or 0,
                "dice_lost": row["dice_lost"] or 0, "dice_gained": row["dice_gained"] or 0,
                "executions": row["executions"] or 0,
            })

        for m in models.values():
            m["win_rate"] = (
                round(m["wins"] / m["tournaments_played"], 3) if m["tournaments_played"] else None
            )
            m["call_accuracy"] = (
                round(m["calls_correct"] / m["calls_made"], 3) if m["calls_made"] else None
            )
            m["spot_on_accuracy"] = (
                round(m["spot_ons_correct"] / m["spot_ons_attempted"], 3)
                if m["spot_ons_attempted"] else None
            )

        recent_rows = conn.execute(
            "SELECT id, seed, started_at, finished_at, live, num_players, total_rounds, "
            "winner_index, winner_archetype, winner_model FROM tournaments "
            "ORDER BY id DESC LIMIT 25"
        ).fetchall()
        recent_tournaments = [dict(r) for r in recent_rows]

        return {
            "tournaments_recorded": tournaments_recorded,
            "rounds_recorded": rounds_recorded,
            "models": sorted(models.values(), key=lambda m: m["wins"], reverse=True),
            "recent_tournaments": recent_tournaments,
        }
    finally:
        conn.close()
