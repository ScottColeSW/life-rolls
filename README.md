# Life Rolls

[![License](https://img.shields.io/github/license/ScottColeSW/life-rolls)](LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/ScottColeSW/life-rolls)](https://github.com/ScottColeSW/life-rolls/releases/latest)

A live Liar's Dice tournament for AI agents. Five or more distinct local
models (via [Ollama](https://ollama.com/)) each get a hand of five dice,
bid against each other round-robin — real quantity + face claims, 1s
wild — and call out bluffs live. Lose a round, lose a die. Lose all your
dice, you're out. Last one standing takes the pot.

Beyond a plain call, players also reach for three real Liar's Dice house
rules: **Spot On** (an exact-match call — stake the whole thing on the
count being *precisely* right, and win a lost die back if you nail it),
**Bidding Aces** / Ace-Switch (a bid can jump onto wild 1s at roughly
half the quantity, since every 1 already counts toward any face — or
jump back off at double the ace count plus one), and **Palafox** (once
someone's down to their last die, the whole round goes special: no wild
ones for anyone, and that player's opening bid is forced to their own
die's true value).

Every real hand is visible on the table the moment it's rolled — no
hidden "?" placeholders waiting on a reveal. The AI players never see
each other's dice, but the human watching does, the whole time.

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
- `engine/history.py` — every tournament gets recorded to SQLite
  (`engine/life_rolls_history.db`, gitignored) as it finishes, backing the
  Standings page (`web/stats.html`, linked from the footer): a per-model
  leaderboard, call/Spot-On accuracy, and a history of every recorded run.
- `web/index.html` + `server.py` — the browser UI: a round table,
  CSS-drawn pip dice (not font glyphs — guaranteed to render the same
  everywhere), a live bid billboard, a roster panel showing each seat's
  assigned model with a live online/offline badge (`GET /api/models`,
  polled periodically), a "thinking" indicator while a live model's real
  Ollama call is in flight, and an honest reveal (every hand visible from
  the moment it's rolled, not just at a call), all streamed live over
  NDJSON as the tournament actually plays out.

`verify_spot_on.py`, `verify_ace_switch.py`, and `verify_palafox.py` are
standalone checks for those three mechanics specifically — hand-computed
expectations against the real engine functions, not part of the app itself.

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

## About the creator

Built by **Scott A. Cole**, an AI strategy consultant and the author of 31 books on AI strategy, GenAI, and
decision-making, including the five-book [**Stop Learning AI** series](https://www.amazon.com/dp/B0GPRFYCQF?&linkCode=ll2&tag=ifio42-20&linkId=b67e3c17a4eb0539b2ec9ec37ef410e4&language=en_US&ref_=as_li_ss_tl) for executives who need to
make good AI decisions without becoming technical themselves. The app includes an **About** page with the full book list ([`web/about.html`](web/about.html)). More projects, including [Aegis Vector](https://github.com/ScottColeSW/Project-Aegis-Vector) and [Palimpsest](https://github.com/ScottColeSW/Palimpsest), are at
[github.com/ScottColeSW](https://github.com/ScottColeSW).

<sub>As an Amazon Associate I earn from qualifying purchases.</sub>
