# Legacy: Split Decision

The earlier version of this project — a 2-player pot/split-or-steal
negotiation game. Fully superseded by the Liar's Dice tournament (see the
repo root's `README.md` and `design/DESIGN.md`). Kept runnable because
deleting working code nobody asked to remove isn't the move, not because
it's part of the current game.

```bash
python legacy/show.py --live           # one negotiation, CLI
python legacy/ladder_demo.py --live    # the old 2-party resolve/bluff ladder
python legacy/pubsub_demo.py --live    # event bus proof, against this older game's events
```

`diagram.html` here was the live event-bus visualization for this game —
it's a static file now, not wired to a running server (server.py only
serves files from `web/`).

The engine code these scripts depend on (`engine/negotiation.py`'s
`run_case`/`decide`/`debate_line`, `engine/resolve_ladder.py`) still lives
at the repo root, not in here — it wasn't moved because
`engine/negotiation.py`'s `OllamaNegotiator`/`ScriptedNegotiator` classes
are shared with the current game (`dice_move` lives on the same classes
as the legacy methods). Untangling that split is real work still owed if
this legacy game is ever fully removed.
