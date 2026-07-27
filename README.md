# Life Rolls

A live Liar's Dice tournament for AI agents. Five or more distinct local
models (via [Ollama](https://ollama.com/)) each get a hand of dice, bid
against each other round-robin — real quantity + face claims, 1s wild —
and call out bluffs live. Lose a round, lose a die. Lose all your dice,
you're out. Last one standing takes the pot.

Zero external dependencies — the whole engine and server are Python
standard library, and the frontend is plain HTML/CSS/JS. No build step,
no install beyond Python itself and (optionally) Ollama.

## Run it

```bash
python server.py
```

Then open **http://localhost:8766** and click **Start Tournament**.
Uncheck "live Ollama" to run instantly on scripted stand-in players
instead (useful for quick iteration, or if Ollama isn't installed).

For a quick terminal-only run instead of the browser:

```bash
python dice_demo.py --players 5 --live
```

Add `--seed N` to replay the same draw, `--force-timeout` to prove a
player gets eliminated for freezing instead of answering in time.

## How it works

- `engine/personas.py` — the persona archetype pool (mandate, temperament,
  private incentive) that seated players get drawn from each tournament.
- `engine/liars_dice.py` — the actual game: bids, calls, elimination, the
  whole tournament loop. Arithmetic (counting matching dice, validating a
  raise) always happens here in Python, never asked of a model — see
  `probe_counting.py` for why.
- `engine/negotiation.py` — the live/scripted player interface: a
  `ScriptedNegotiator` (instant, dependency-free) and an `OllamaNegotiator`
  (a real local model, falling back to scripted on any failure — except a
  live timeout during a bid, which is a real elimination, not a fallback).
- `engine/eventbus.py` + `engine/tournament_watchers.py` — a small
  pub/sub layer: the tournament publishes events, and independent
  watchers (State, Stats, a Highlight filter, a Host that narrates only
  what the filter surfaces) subscribe without the engine knowing any of
  them exist.
- `web/index.html` + `server.py` — the browser UI: a round table, real
  dice-face glyphs, a live bid billboard, and an honest reveal (every
  hand shown, not just the computed count).

See [`design/DESIGN.md`](design/DESIGN.md) for the full design history —
why it's built this way, what's been tried and reverted, and what's
still open.

## What's in `legacy/`

An earlier version of this project ("Split Decision") was a 2-player
pot/split-or-steal negotiation game. Liar's Dice replaced it entirely —
`legacy/` keeps that game's CLI scripts runnable for reference, but
nothing there is part of the current game.

## Requirements

Python 3.9+, no pip packages. [Ollama](https://ollama.com/download) is
optional — without it, every player runs on the scripted stand-in agent.
