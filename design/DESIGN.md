# Life Rolls — Design Doc (v1: Liar's Dice)

**This project is Life Rolls now, not Split Decision.** The pot/case/
split-steal framing below (kept for its own historical record) was the
earlier idea; a real, authentic multi-round Liar's Dice tournament —
quantity+face bids, round-robin among 5+ seated players, elimination on a
lost round, last one standing takes the whole pot — is the actual game.
See "Liar's Dice tournament (implemented)" further down for the current
mechanic. The repo folder, page titles, GitHub repo (ScottColeSW/life-rolls,
public, `main` branch), and port env var are all renamed to match now.

## Real-time streaming (implemented) — replaces precompute-then-replay

`server.py`'s `/api/run-tournament-stream` + `web/index.html` fixed a real
reported bug: "Start Tournament seems to hang." Root cause was the
original design (server computes the ENTIRE tournament, including every
live Ollama call across a dozen-plus rounds, before responding at all) --
in live mode that's a genuinely multi-minute wait with zero feedback on
screen. The fix is real streaming, not a faster replay: a "preflight"
status line per real setup step (model assignment, etc.) before anything
else, then every raw tournament event AS it happens via
`run_tournament_on_bus` + `HighlightWatcher`, both flowing through one
relay since the relay just subscribes to everything on the bus, including
what `HighlightWatcher` publishes back onto it. The browser's `EventQueue`
class decouples "how fast events arrive" (instant in scripted mode, paced
by real latency in live mode) from "how fast they're displayed" (a floor
pace either way, so scripted mode doesn't blur past and live mode never
waits longer than the real call already took). A curtain-wipe reveals the
fully-assembled table once the first round of setup completes, per
Scott's ask for "pre-production info live... then screen wipe/curtain,
and board assembly." Board state (billboard, active seat, dice) reacts to
every raw event; the round-log ticker only ever shows `HighlightWatcher`'s
curated output -- the same "board sees everything, host only narrates
what's notable" split proven earlier now actually wired into the real UI,
not just a demo script.

Two real bugs found and fixed while building this, neither from guessing:

- **A significant, previously-undiscovered game-logic bug**: `run_round`'s
  loser calculation was backwards -- `loser_idx = claimant_idx if
  bid_was_true else current_idx` silently rewarded bluffing and punished
  honest calls in every tournament run before this fix (copied the shape
  of `resolve_ladder.py`'s `winner_id = claimant_id if claim_was_true else
  caller_id`, correct there since it computes the WINNER, without flipping
  the branches for computing the LOSER instead). Caught while reasoning
  through the new streaming client's event handling, not by accident --
  verified with a targeted script checking 60 calls (9 true, 51 bluff)
  against the logically-correct outcome, zero mismatches after the fix.
- **The streamed `round_call` event didn't include `hands`** (only the
  final `RoundResult` object did) -- the new client's reveal code assumed
  it was there and threw `Cannot convert undefined or null to object`,
  which silently killed the display loop with no console output visible
  in this session's browser tooling (an uncaught rejection in a
  fire-and-forget async call). Found by temporarily instrumenting
  `handleStreamEvent` to log every event kind and catch/report exceptions
  rather than guessing from symptoms. Fixed by adding `hands=snapshot` to
  the `emit("round_call", ...)` call itself, verified via curl against
  the raw stream before ever going back to the browser.

Verified end to end in both modes after both fixes: scripted (134 events,
zero errors, correct winner/elimination count) and live (14 real rounds,
model assignments visible within 2 seconds of clicking, zero errors,
correct winner).

## Liar's Dice tournament (implemented, replaces the old resolve ladder + split/steal)

`engine/liars_dice.py` (`Bid`, `PlayerState`, `RoundResult`,
`TournamentResult`, `run_round`, `run_tournament`), plus `dice_move` on
both negotiator classes in `engine/negotiation.py`. Proven via
`dice_demo.py` (`--seed N`, `--players N`, `--live`, `--force-timeout`).

- Each of N seated players (5+) gets a hand of dice — `HAND_SIZE = 5`,
  the standard Liar's Dice rule. Was `3` (a deliberate pacing shortcut,
  fewer dice per player meaning faster rounds and less to track visually)
  until Scott cross-checked this engine against researched real rules and
  asked to match the standard. A full tournament now needs a structural
  minimum of `(players-1)*5` lost rounds before one winner remains,
  meaningfully more than at 3, so a full run naturally takes longer —
  accepted tradeoff, not an oversight.
- **1s are wild** (standard Liar's Dice rule) — count toward any face bid.
- Bids are real `(quantity, face)` pairs, round-robin turn order among
  however many players are still alive. A raise must strictly increase a
  simple encoded ordering (`quantity * 7 + face`) — deliberately NOT
  implementing the classic halve-when-switching-to-1s house rule some
  variants use; a documented simplification, not a bug.
- **A call reveals every hand**, counts the real total for the claimed
  face (wilds included), and compares to the bid. Wrong bidder or wrong
  caller loses one die. Zero dice = eliminated. The round's loser opens
  the next round if they survive (real convention — same player who just
  lost nervously has to open again); if eliminated, the next player in
  rotation opens instead.
- **Last player standing takes the whole pot.** No separate split/steal
  step — the tournament itself is the resolution.
- **Arithmetic never happens in the model** — same `probe_counting.py`
  finding as before, now covering `qwen2.5:7b`, `phi4-mini`, and
  `gemma4:12b` too: every locally-runnable model tested has a real,
  stubborn weakness at basic counting/addition, independent of size. The
  model is only ever asked a qualitative move (RAISE_SAME / RAISE_NEW /
  CALL); `liars_dice.py`'s own helpers compute every actual bid value.
  `run_round` also has an engine-side safety net: if a live model's raise
  doesn't actually beat the standing bid (the same arithmetic slip),
  that's treated as a freeze — executed, not silently corrected.
- **A live timeout (or an illegal bid) is an immediate, full elimination**,
  not a graceful scripted fallback — same deliberate exception as the old
  ladder (Scott: "people get bored waiting... execution if they fail to
  play in time"). Verified two ways: a genuine *unforced* execution
  happened live during ordinary testing (a real model actually froze),
  and a `--force-timeout` run confirmed the path fires deterministically
  every time, eliminating every player in turn order exactly as designed.

Known rough edges, honest and not yet fixed: live models tend to call
bids too eagerly even when the bid is actually true (every call in one
live run resolved as bid-was-TRUE; every call in a separate scripted run
resolved as a bluff) — a real calibration question for the scripted
heuristic's tolerance, not an engine bug. Some live "reason" text is
truncated/garbled from the tight token budget, same known category of
issue as the old ladder's reasons.

**Web UI (implemented, v0):** `web/index.html` fully rebuilt for the
tournament — trigonometric round-table seating (real distinct positions
for N players, not just 2 fixed sides), a billboard tracking the live
standing bid, turn highlighting, an honest dice reveal (actual hand
values, not just the computed count) using `RoundResult.hands`, and
elimination/winner visuals. Dice faces are real CSS-drawn pip patterns (a
3x3 grid of `.pip` divs per `.die-face`), not Unicode glyphs (⚀-⚅) —
switched after Scott reported the glyphs weren't legible ("I can't see
either the table hand or the players hands. i need to see dice."), since
Unicode die-face rendering isn't guaranteed across systems/fonts while a
CSS pip grid always renders identically. Superseded the original
precompute-then-replay transport with real NDJSON streaming — see
"Real-time streaming" above.

**Spot On (implemented):** the exact-match side bet researched and
cross-checked by Scott against real Liar's Dice rules ("Calza"). A player
can call `spot_on` instead of a plain `call` on the standing bid — if the
real count across every hand matches the bid's quantity EXACTLY, the
claimant is caught out (loses a die, same as a normal successful call)
*and* the caller separately wins a previously-lost die back, capped at
`HAND_SIZE`; a wrong Spot On (high or low) costs only the caller, same as
a wrong plain call. Both branches live in `run_round`'s `spot_on` case in
`engine/liars_dice.py`, with `RoundResult.gained_die_index` /
`is_spot_on` / `spot_on_correct` carrying the extra state, and
`run_tournament` applying the `min(HAND_SIZE, ...)` gain separately from
the normal loser decrement.

The first implementation only ever affected the caller (gain on success,
loss on failure) — corrected before shipping once Scott supplied a
worked example showing the claimant is *also* penalized on a successful
Spot On ("Player 4 is penalized, and you get to reclaim a previously
lost die"). `ScriptedNegotiator.dice_move` only considers Spot On when
the expected count sits almost exactly on the bid (a tight gap, tighter
than its plain-call tolerance) and there's an actual die to win back
(`len(hand) < HAND_SIZE`); `OllamaNegotiator.dice_move`'s live prompt
only offers SPOT_ON as a word option under that same gate. Verified via
`verify_spot_on.py` (a successful call, two failed calls at different
directions, and a tournament-level check that the `HAND_SIZE` gain cap
actually engages), then confirmed live in the browser end to end — both
a failed and a successful Spot On fired correctly with zero console
errors, curated into Host commentary by `HighlightWatcher` (climax level
on a hit, major on a miss) with a distinct gold reveal-flash and sound
cue (`sfxSpotOnHit`/`sfxSpotOnMiss`) separate from a normal call's
green/red.

**Bidding Aces / Ace-Switch (implemented):** the house rule letting a
raise cross onto or off of wild 1s using its own formulas instead of the
plain quantity/face ordering, since a bid on 1s is worth roughly double
an ordinary face (every 1 already counts toward any face bid) — also
researched and cross-checked by Scott. Switching a bid ONTO aces only
requires half (rounded up) of the standing quantity:
`ceil(standing.quantity / 2)`. Switching a bid OFF of aces requires
double the ace quantity plus one: `standing.quantity * 2 + 1`. Both
formulas live in `_valid_raise` (the legality check every raise goes
through, including a live model's) and `compute_ace_switch_raise` /
`compute_new_face_raise` (the latter now branches on `standing.face ==
WILD_FACE` for the off-aces case) in `engine/liars_dice.py`. This is the
halve-when-switching-to-1s rule that `Bid.value()`'s docstring originally
flagged as a deliberate first-version simplification, not a bug — now
implemented for real.

`ScriptedNegotiator.dice_move` only proposes an Ace-Switch when NOT
already standing on aces and when this player's own wilds plus the
expected wilds among every other die comfortably cover the halved
quantity — otherwise it'd just be handing the table a bid that looks
strong but isn't backed by anything, a worse bluff than an ordinary raise.
`OllamaNegotiator.dice_move`'s live prompt offers `RAISE_ACE` as a word
option under the same gate, still without ever asking the model to state
or compute the actual number (`compute_ace_switch_raise` always does
that, matching the project-wide "arithmetic never happens in the model"
rule). `HighlightWatcher` treats crossing the ace boundary (either
direction) as always notable regardless of the raw quantity delta — a
switch onto aces typically *lowers* the displayed quantity, which would
never clear the ordinary `BIG_QUANTITY_JUMP` bar even though it's exactly
the kind of bold, unusual move worth surfacing.

Verified via `verify_ace_switch.py` (both formulas' exact boundaries —
one below the minimum is illegal, exactly at the minimum is legal — plus
a `run_round`-level check that an illegal Ace-Switch bid gets executed on
the spot rather than silently corrected, the same treatment as any other
engine-side illegal-raise catch), then confirmed live in the browser:
both switch directions fired and narrated correctly across a full
tournament, including a Spot On call landing exactly on an ace bid,
with zero console errors.

**Palafox/Showdown (implemented):** the forced-exact last-die special
round, researched and cross-checked by Scott alongside Spot On and
Ace-Switch. When the round's opener is down to exactly one die
(`PALAFOX_TRIGGER_DICE`), the whole round becomes special: 1s stop being
wild for EVERYONE (not just the one-die player), and that player's
opening bid is forced to their own die's true value — `Bid(quantity=1,
face=<their actual roll>)` — bypassing their negotiator entirely, since
there's no real decision to make (or bluff to attempt) with a single die.
`run_round` decides this once, up front, from `dice_remaining` alone
(before any hand is even rolled), and threads a `wild_active: bool`
through every counting/validation function for the rest of that round:
`_count_matching`, `_valid_raise`, `compute_new_face_raise`, and both
negotiator classes' `dice_move`. `RoundResult.is_palafox` carries the flag
through to the tournament loop and every event.

Crossing the ace/non-ace boundary during a Palafox round is deliberately
*not* run through the Ace-Switch halve/double-plus-one formulas — with
wilds off, face 1 is just an ordinary (weak) face, so the plain ordinal
comparison in `Bid.value()` already handles it correctly with no special
case. `HighlightWatcher` tracks `_is_palafox_round` specifically so it
doesn't mislabel an ordinary face-1 raise as an "Ace-Switch" during a
no-wild round. The forced opening and the round-start itself both always
surface as climax-level highlights regardless of the usual noise
filtering — a Palafox round changes how the whole round should be read,
so the human needs to know before the forced bid even lands, not just
after the fact.

Verified via `verify_palafox.py` (wild-bonus disabled correctly in
`_count_matching`, no halving/doubling through the ace boundary in
`_valid_raise` when `wild_active=False`, a `run_round`-level check that
the forced opening bid exactly matches the one-die player's real roll
without ever consulting their negotiator, and a contrast check that an
ordinary round with a 5-die opener is never mistaken for Palafox), then
confirmed live in the browser across a full tournament: the round-label
itself flags "PALAFOX -- no wild ones," the forced opening and the
no-wild resolution both narrated correctly, and the tournament finished
cleanly with zero console errors.

This closes out all three researched house rules (Spot On, Bidding Aces,
Palafox) — the "a little at a time" sequencing Scott asked for is done.

## Dice visibility: real values, not hidden "?" placeholders (implemented)

Every hand used to stay hidden (`?` glyphs) until a round's reveal moment
(a call, a Spot On, or an execution) — deliberately withholding the truth
for suspense, mirroring what the AI players themselves can't see. Scott
pushed back: "I'd rather we see them all the time; before, during, and
after each roll. As the human I need to see the choices and bluffs more
visually" — the suspense was actively working against the stated goal of
this whole project (showing a human what these AI agents are actually
doing), not for it.

Fixed by moving the reveal earlier: `run_round` now sends every alive
player's real hand (`hands={i: list(players[i].hand) ...}`) on the
`round_start` event itself, the moment dice are rolled — not just at the
end of a round. The client renders real pip-dice immediately on
`round_start` (no `matchFace` yet, since there's no bid to compare
against) and keeps them on screen through the whole round; the existing
reveal events (`round_call`, `round_spot_on`) still re-render with
`matchFace` set, to highlight which pips actually matched the bid. This
let an entire layer of client bookkeeping disappear: `diceCounts` and
`renderHiddenDice()` are gone completely -- the dice count for a seat is
now just `hand.length` from whichever real hand was last sent, so there's
nothing to keep in sync by hand anymore (the exact kind of duplicated
state that already caused two separate bugs earlier in this project).
`player_eliminated` now also explicitly clears the seat's dice row, so a
dimmed-out eliminated player doesn't keep showing a stale "ghost" hand
from the round that finished them off.

## Tournament history / Standings page (implemented)

Scott: "it would be great if we could have some stats on a page that
will capture and show metrics and history of all runs (we did this in
the Dominion project)." Mirrors Dominion's own `engine/history.py` +
`web/stats.html` almost exactly, adapted to this game's own event
vocabulary rather than shared code -- the two projects' event shapes
have nothing in common beyond the general pub/sub pattern.

`engine/history.py`: a SQLite schema (`tournaments`, `player_stats`) and
`TournamentHistoryRecorder`, a plain synchronous object fed the exact
`(kind, data)` stream `server.py` already relays to the browser --
`recorder.on_event(event.kind, event.data)` sits right alongside the
existing `sink(...)` call in `_run_streamed_tournament`'s relay loop, the
same shape as Dominion's `recorder.on_event(ev)` next to its own
`write_event`. No new async bus subscription needed -- this recorder
isn't a `tournament_watchers.py`-style watcher, just a callback chained
into the same relay everything else already flows through. `on_event`
never raises (a storage hiccup should never be able to interrupt a live
tournament over what's ultimately a nice-to-have), and one row per
tournament plus one row per seated player gets written once
`tournament_winner` lands. Aggregated by MODEL, not player index or
archetype (both redrawn fresh every tournament) -- a scripted player's
`model` is `None` and is excluded from the leaderboard entirely, the
same convention Dominion's `get_stats()` uses.

Tracks, per player per tournament: calls made/correct, Spot Ons
attempted/correct, raises made, Ace-Switches made, dice lost/gained,
executions, and which round they were eliminated in (if at all). The
Ace-Switch detection is its own independent copy of the same
face-crossing check `HighlightWatcher` already does for narration --
duplicated on purpose rather than shared, matching this file's existing
"the two watchers are independent by design" stance elsewhere in this
doc.

`web/stats.html`: a Standings page (linked from the main page's footer)
showing a summary bar, a per-model leaderboard, per-model "player cards,"
and a Recent Tournaments list -- the last one specifically because Scott
asked for "history of all runs," not just an aggregate leaderboard, which
is closer to what Dominion's own stats page shows.

Verified by running one scripted and one live tournament concurrently
through the actual server (not a mocked test) and confirming both landed
in `/api/stats` with correct model attribution, round counts, and
winners -- zero console errors on either the game page or the Standings
page.

## Roster panel + "thinking" indicator (implemented)

Scott: "instead of a long string of our competitors at the top, can we
have a player/model/online list badge on the side of the play board? We
also did something like this in Dominion. I'd like to see a 'thinking'
icon when the model is invoked for a turn." Two changes, same request:

**Roster panel** (`#roster-panel` in `web/index.html`): replaces the old
single run-on status line ("live: A -> modelX, B -> modelY, ...") with a
proper per-seat row (name, model, an online/offline dot) in a panel
beside `#table-wrap` inside a new `.board-row` flex wrapper. Backed by a
new `server.py` endpoint, `get_model_roster()` / `GET /api/models` --
cross-references `TEXT_MODELS` (the actual pool a live tournament draws
from) against Ollama's own `/api/tags`, mirroring Dominion's
`get_model_roster()` almost line for line. The client polls this every
5 seconds regardless of whether a tournament is running, same reasoning
as Dominion: whether Ollama is up/down or a model is installed can
change while the page stays open.

**Thinking indicator**: `run_round` now emits a `player_thinking` event
immediately before calling `player.negotiator.dice_move(...)` for a
turn (skipped entirely for a Palafox player's forced opening, since
there's no negotiator call at all there to precede). Purely
informational -- no effect on game logic. The client shows a small
pulsing badge on that seat only when it has a live model assigned
(`current.models[i]` truthy and `current.live`); a scripted negotiator
resolves instantly, so the badge would flash for at most one frame
either way, but gating it avoids needless flicker. Cleared via a shared
`clearThinking()` helper alongside every definitive per-turn event
(`round_raise`, `round_call`, `round_spot_on`, `round_execution`).

Verified live: ran a real live tournament and confirmed the badge tracked
the correct seat as each live model's actual Ollama round-trip was in
flight, the roster panel showed accurate per-seat model assignments with
live "online" dots (cross-checked against `/api/models` returning all
five `TEXT_MODELS` installed and reachable), and zero console errors
across the full run.

## Highlight/Host watchers (implemented) — the "board sees everything, Host only narrates what matters" split

`engine/tournament_watchers.py`: `TournamentStateWatcher` and
`TournamentStatsWatcher` subscribe to the **raw, unfiltered** event
stream and update unconditionally — the board must reflect everything,
live, regardless of what's "interesting." `HighlightWatcher` is the new
piece — Scott: "a model output filter, limiting emissions to interesting
choices or decision chains, not just a stream of consciousness." It
doesn't filter model text (nothing here is free text to begin with,
every engine action is already a structured event) — it curates which
EVENTS get surfaced as spoken commentary: every `round_call` /
`round_execution` / `player_eliminated` / `tournament_winner` is
inherently rare and always surfaced; `round_raise` is the noisy one, so
only a round's opening bid and a genuine quantity leap (`BIG_QUANTITY_JUMP
= 2`) get flagged — everything else still reaches Stats, it just doesn't
interrupt commentary. `TournamentHostWatcher` — the concrete version of
"Host should showrun, not just react to raw events" — subscribes to
`HighlightWatcher`'s own published `highlight` events, not the raw
firehose; `HighlightWatcher` publishes back onto the same bus it
subscribes to, which is what makes this possible without Host needing a
copy of the filtering logic or the engine knowing either watcher exists.

Real bug caught and fixed by actually measuring the ratio, not assuming
the filter worked: the first cut compared `Bid.value()` directly (a
`quantity*7+face` encoding built for strict-ordering checks, not
"how big is this jump" semantics) and surfaced 71 of 78 raw raises —
barely filtered anything, since quantity is weighted 7x face in that
encoding and even a routine `quantity+1` raise cleared the threshold.
Fixed by tracking the actual `Bid` object and comparing the real quantity
delta instead; re-verified at 22 of 78 (~28%) surfaced in the same
scenario. Proven via `tournament_pubsub_demo.py` in both scripted and
live mode.

**Not yet built:** wiring this bus/watcher pattern into the live browser
UI (still a separate precompute-then-replay flow, not real streaming),
and the "transparency window" itself — a live "model is deciding right
now" indicator tied to actual wait time, discussed but not yet started.

---

# Split Decision — Design Doc (v0) — superseded, kept for history

## The pitch

Two teams of AI agents are handed a case — a dispute, a deal, a shared pot
of stakes — and have to negotiate it live, on air. When the debate ends,
each team secretly chooses to **split** the pot fairly or **steal** it
outright. Both choices reveal at once. Before any of that plays out, the
human audience locks in a prediction: will they split, or will someone
steal?

Real precedent this borrows from directly: Golden Balls' "Split or Steal"
segment — same split/steal tension, still one of the most-replayed game
show moments ever made, purely because "will they betray each other" is a
hook humans don't look away from. This adds teams, a live debate phase, a
rotating cast of negotiation scenarios, and a prediction game layered on
top.

## Why this shape (lessons carried over from Dominion)

Dominion (the sibling project) spent a long revision history discovering,
the hard way, that small local models are **good at persuasive dialogue**
(bluffing, pitching, reacting in character) and **bad at multi-step planning
and state-tracking across turns** (Dominion's agents repeatedly confused
their own stated facts, needed the same information re-fed every single
call, contradicted their own verdicts mid-sentence). See
`../Claude-Agent Orchestrated Game Show Sim/prototype/engine/ollama_agent.py`
revisions 22-34 for the receipts.

Split Decision is deliberately built to play to the strength and avoid the
weakness:

- **Negotiation is persuasive dialogue** — exactly what these models are
  good at.
- **The split/steal choice is a single, one-shot decision**, not a
  multi-turn plan the model has to hold together. No deep lookahead
  required.
- Because the underlying incentive structure is a well-studied game-theory
  scenario (see below), a live **host can legitimately narrate the
  strategy** as it happens — "this is shaping into a classic Ultimatum
  lowball, and lowballs usually get rejected out of spite" — which adds a
  legible layer for viewers who don't know game theory, and a wink for
  those who do. It also means an obvious, spottable Prisoner's-Dilemma
  read isn't the only shape a case can take.

## Core loop (per case)

1. **Case revealed** — the scenario and the pot are shown to teams and
   audience together. Each agent gets a **thought bubble**: their gut
   read/true incentive, shown to the audience only (see Transparency
   below).
2. **Predict window** — the human audience locks in a guess (split / steal,
   maybe a guessed split %) *before* the debate plays out.
3. **Face-off debate** — each team (2-3 agents, private caucus + a
   spokesperson) makes its live case, back and forth, host moderating.
4. **Second thought bubble** — right before the secret decision, each agent's
   real intention, whatever they're about to publicly claim. This is the one
   that gets checked against what they actually do.
5. **Secret decision** — each team privately chooses split or steal.
6. **Simultaneous reveal** — both choices shown at once; pot resolves.
7. **Score** — the pot resolves for the teams; the audience's predictions get
   scored against the outcome; each agent's second thought bubble gets
   scored against their actual choice (see Scoring & measurement below).

## The case library: rotating game-theory frameworks

Same shell every episode (negotiate over a pot, predict the outcome),
different underlying incentive structure each time — this is what gives
the host something fresh to narrate, and keeps the show from reading as
"the same Prisoner's Dilemma with a new coat of paint" after a couple of
episodes. Candidate frameworks to build cases around, roughly in order of
how soon we'd build them:

- **Ultimatum Game** — one side proposes a split, the other can only accept
  or blow it up for both. (e.g. a merger dispute.)
- **Trust Game** — one side has to move first and expose themselves before
  the other responds. (e.g. a hostage-style ultimatum.)
- **Stag Hunt** — mutual cooperation pays best, but it's individually safer
  to defect. (e.g. a joint venture requiring both sides to invest.)
- **Chicken** — whoever backs down first loses face; neither backing down
  is disaster for both. (e.g. a standoff over shared territory or a
  deadline.)
- **Public Goods Game** — later; needs more than two parties to land well.

Each case needs: a human-legible scenario/backstory, a pot size, and a
payoff structure that actually matches the named framework underneath —
this is content-authoring work, same shape as Dominion's `content.py`
domain library, just game-theory scenarios instead of trivia questions.

## Persona trio (agents)

Reusing the exact pattern that made Dominion's agents read as characters
instead of random number generators — profession, temperament, and
origin-domain affinity together, not any one alone — re-aimed at
negotiation:

- **A grounded mandate** (was: profession) — who/what this agent actually
  represents, and *why this specific pot matters to them*. Real,
  character-driven stakes instead of generic self-interest.
- **A temperament axis** (was: temperament) — re-tuned for this game: risk
  tolerance and trust propensity, since those are what actually drive
  split-vs-steal behavior, not aggression/caution.
- **A private true incentive** (new) — something the agent knows that its
  opponents don't, which may or may not match what it's publicly arguing.
  Real information asymmetry: something legitimate to bluff about or
  reveal strategically.

## Transparency: thought bubbles

Decided: **the human audience sees everything, the opposing team sees
nothing.** That's the actual source of the drama — the audience knows an
agent's real incentive and real intention going into the reveal, the other
team is negotiating blind, same as the "will they betray each other" tension
that makes Split or Steal work in the first place.

Mechanism: a literal thought-bubble graphic over an agent's portrait,
firing at the two moments in the core loop above — the initial gut
read/incentive right after the case is revealed, and the real intention
right before the secret decision. Comic-style, legible at a glance, not a
paragraph — same instinct as Dominion's blurt animation: a quick, readable
beat, not a wall of text mid-show.

## Scoring & measurement

This is the "humans see it all and measure it" answer, same instinct as
Dominion's `stats.html` Standings page — a persistent leaderboard, not just
whatever's on screen right now. Tracked per agent/team, across every case,
every show:

- **Split/steal rate** — how often each agent actually splits vs. steals,
  over time. The core integrity read.
- **Said vs. did** — the new metric this design adds: how often an agent's
  *second* thought bubble (their stated real intention, right before
  deciding) matches what they actually chose. An agent that's consistently
  honest with the audience is a different character than one who's
  consistently self-deceiving or bluffing even in their own "private"
  thoughts — both are watchable, but they're distinct, measurable
  personalities.
- **Audience prediction accuracy** — scored the same way, over time, so
  regular viewers can see whether they're actually getting good at reading
  these agents or just guessing.
- **Pot totals won** — the straightforward leaderboard number.

## Cast and stakes principles

- **Regular people, not just executives.** The persona pool spans ordinary
  American working life alongside corporate/institutional roles — a diner
  owner, a night-shift nurse, a long-haul trucker, an immigrant shop owner,
  a gig worker — a range of ages and backgrounds, not a cast of CFOs and
  diplomats.
- **"Death on the line," always — but not always literally.** Every case
  needs a stake the audience can feel as real, irreversible loss, drawn
  from one of four registers, deliberately mixed across episodes rather
  than picking one tone and sticking to it:
  - *Literal* — real physical danger, always fictionalized, never tied to
    an actual current event.
  - *Social/psychological* — public humiliation, exposure, losing a
    reputation built over a lifetime.
  - *Existential/livelihood* — losing the thing that IS you: the family
    business, the career, the life's work.
  - *Absurd-but-earnest* — a beloved snack, a parking spot — played
    completely straight-faced by the agents even though the audience knows
    it's silly. This register is the tone escape valve; lean on it whenever
    the show needs to breathe.
- **No real-world political conflicts as case material.** Cases are
  personal, workplace, family, or community-scale, or clearly
  fictional/absurd — never an actual ongoing real-world dispute. Keeps
  "death on the line" stakes feeling real without the show ever reading as
  taking a side on something genuinely contested.

## Persona system (implemented)

`engine/personas.py` is the first real code in this repo. It implements the
mechanism described above: `MandateArchetype` is the authored content (11
to start — The CFO, The Executor, The Union Rep, The Founder, The Diplomat,
The Adjuster, The Diner Owner, The Night-Shift Nurse, The Long-Haul Trucker,
The Immigrant Shop Owner, The Gig Worker), each carrying a risk-tolerance
range, a trust-propensity range, and a pool of private-incentive templates
rather than fixed values. `draw_persona` rolls one concrete `Persona` from
an archetype — fresh temperament within range, one incentive template
picked — so the same archetype produces a different, but recognizably
similar, character each time it's drawn. `draw_cast(count, rng)` is the
actual per-episode entry point: draws `count` distinct archetypes and rolls
a Persona from each. Expanding the cast is just adding another
`MandateArchetype` entry to `ROSTER` in the same shape; adding more variety
to an existing one is just adding another string to its
`incentive_templates` list — no other code changes needed, same growth
pattern as Dominion's domain library.

## Core loop (implemented, v0)

`engine/cases.py`, `engine/negotiation.py`, and `show.py` implement the
case/gut-read/debate/real-intent/decide/reveal loop end to end, runnable
tonight with `python show.py` (add `--predict` to pause before the reveal,
`--live` to use Ollama-backed negotiators when reachable, auto-falling back
to scripted otherwise — same safety pattern as Dominion's `OllamaAgent`).
Verified working in both modes. Known v0 scope cuts, deliberate, not bugs:

- **One negotiator per side, not the 2-agent teams-with-caucus** from the
  Cast principles section — simplest version of the loop first, teams are
  the natural next step.
- **Scripted debate lines are a single fixed template per persona**, so
  `debate_rounds` is capped at 1 in scripted mode (a second round would
  repeat verbatim) vs. 2 in live mode (models never repeat exactly). More
  scripted template variety would let this go back up.
- **Live `real_intent` occasionally falls back to the scripted line** when
  a model's reply doesn't cleanly start with SPLIT/STEAL — the fallback
  still produces a valid said-vs-did result, just not a live-flavored one
  for that beat.
- 4 cases in `CASE_LIBRARY` so far (one per framework), all
  existential/absurd register — no literal-danger case yet.

## Browser UI (implemented, v0)

`server.py` + `web/index.html` — zero-dependency stdlib server (mirrors
Dominion's own `server.py`) serving a single-page frontend, replacing the
CLI's wall of text with persona cards (avatar, mandate, risk/trust bars),
real thought-bubble graphics per the Cast principles section, debate as
chat bubbles, click-through predict/debate/reveal staging, and a
prediction-correct/wrong readout. `python server.py`, then
`http://localhost:8766`, click "Run a Case." Verified end to end live in
browser (not just assumed): confirmed a real UTF-8 encoding bug (missing
charset on HTTP responses, mangled the arrow character) and a real prompt
problem (live `real_intent` replies rambling between SPLIT/STEAL language
unfiltered) — both fixed and re-verified in the running page, not just in
isolation.

## Event bus / pub-sub (implemented, v0)

`engine/eventbus.py`, `engine/watchers.py`, `engine/live_case.py`, and
`pubsub_demo.py` (`python pubsub_demo.py`, add `--live` for real Ollama
calls) implement the pattern discussed above: the negotiation engine
publishes an ordered event stream and never knows or imports a single
consumer of it. Four watchers subscribe independently — **Audience**
(reacts to public events only, never the private thought bubbles, keeping
the "in-show crowd is just another agent, not the human" framing
coherent), **Host** (commentary keyed to the case's game-theory framework,
the "calls out the strategy" idea), **Stats** (records outcomes across
every case — the "measure it all" leaderboard, in-memory for now), and
**State** (the current live snapshot, for a client that connects
mid-case). Adding a fifth watcher later — a highlight-reel detector, a
notification hook — is one more class in the same shape; nothing in
`engine/negotiation.py` changes to support it.

Real correctness detail worth keeping in mind: `run_case` itself is still
synchronous (real blocking Ollama HTTP calls), so it runs inside a worker
thread via `asyncio.to_thread`, publishing into the bus through
`call_soon_threadsafe` since asyncio queues aren't safe to push into from
a thread that isn't the event loop's own. And every watcher's queue is
subscribed *synchronously, up front*, before the case starts publishing —
subscribing lazily inside each watcher's own coroutine would race the
case's first event with no guarantee of winning. Verified in both
scripted and live mode: live mode is the real test, since it's the one
where a worker thread is genuinely blocked on network I/O while the event
loop fans out to four consumers concurrently.

## Live diagram (implemented, v0)

`web/diagram.html` + `server.py`'s `/api/run-case-stream` endpoint wire
the bus into the browser for real: `DiagramRelayWatcher` (in
`engine/watchers.py`) is a genuine fifth subscriber, not a separate
mechanism faking one — it formats each event into a label + which
watchers it actually reaches, and hands that to a sink that `server.py`
wires straight to an NDJSON line over the existing (still-synchronous)
stdlib server, one `asyncio.run()` per request. The page animates a pulse
from Engine to Bus, then Bus to each real target node, live, as an actual
case runs — `python server.py`, open `/diagram.html`, click "Run a Case."
Verified: real event stream arrived end to end in live mode (console
clean, full case_reveal → gut_read ×2 → debate_line ×4 → real_intent ×2 →
reveal → case_end sequence logged), and the SVG line geometry resolves to
real, distinct, non-degenerate coordinates matching each node's actual
screen position — not independently confirmed by eye (this session can't
screenshot the Browser pane), so the animation's actual feel is worth
your own look before calling it done.

This is genuinely useful beyond a demo: multi-viewer streaming (each
connected browser as its own bus subscriber, watching the same live case)
is the natural next use of the same pattern, once the async-server
decision (FastAPI/uvicorn vs. staying stdlib) gets made deliberately
rather than assumed.

## Resolve/bluff ladder (implemented, v0)

`engine/resolve_ladder.py` + `ladder_move` on both negotiator classes in
`engine/negotiation.py`, proven via `ladder_demo.py`
(`--seed N` / `--live` / `--force-timeout`). Each side gets a hidden
resolve value (1-10, rolled fresh per case); claims escalate about the
COMBINED total, which neither side can be fully certain of, mirroring
real Liar's Dice's "you know part of the truth, not all of it" tension
with one hidden number per side instead of a cup of five.

Two decisions worth recording the reasoning for:

- **Arithmetic never happens in the model.** `probe_counting.py` tested
  every locally-pulled model (four original + `qwen2.5:7b`, `phi4-mini`,
  `gemma4:12b`) on basic numeric comparison and addition — all cluster at
  3-4 out of 5 correct regardless of size, with the same class of mistake
  (real addition, not just comparison) tripping up every single one.
  `gemma4:12b` came back completely empty even with a 120-token budget and
  90s timeout — confirmed broken through this calling convention, not
  just slow. Conclusion: this is a model-selection-proof weakness, so the
  ladder never asks a model to state or validate a number — only a
  qualitative RAISE_SMALL / RAISE_BIG / CALL, with Python computing every
  actual claim value and comparison.
- **A live timeout is an in-game loss, not a graceful fallback.** Every
  other live call in this project (attempt_question, decide, debate_line,
  real_intent) degrades to a scripted default on failure so the show
  never stalls. The ladder is a deliberate exception — Scott: "people get
  bored waiting for 'thinking'... execution if they fail to play in
  time." `LADDER_TURN_TIMEOUT` is 8s (far tighter than the project's
  usual `OLLAMA_TIMEOUT`), and a live negotiator that times out or returns
  an unparseable reply raises `LadderTimeout`, which `run_ladder` turns
  into an immediate loss for whoever froze — "executed" by their own
  hesitation. Verified with `--force-timeout` (an artificially
  impossible 0.001s timeout), confirming the execution path actually
  fires and ends the game, not just assumed to work from reading the code.

Model roster updated: `qwen2.5:7b` added (ties the top accuracy tier at
reasonable latency, same family that already produced good dialogue).
`phi4-mini` and `gemma4:12b` were pulled and tested but left out —
`phi4-mini` showed no measurable edge despite 2-4x the latency;
`gemma4:12b` is confirmed unreliable through the plain `/api/generate`
call this project uses.

**Now wired into the main show, verified live in the browser, not just in
isolation.** `run_case` runs the ladder between the debate and the final
decision; the winner gets `STEAL_BIAS_WINNER` (-0.05, feels secure) and
the loser gets `STEAL_BIAS_LOSER` (+0.15, feels rattled/cornered) applied
to their `decide()` call, asymmetric on purpose since getting caught
bluffing (or freezing) is a bigger emotional hit than winning is a boost.
A live negotiator's prompt gets a short flavor line about what just
happened (`_ladder_note`) so its own reasoning can react to it, not just
have its odds nudged invisibly. `web/index.html` now has a real "resolve
stand-off" section between the debate and the reveal, and the final
reveal states both true resolve numbers and the verdict. One real bug
caught and fixed before this ever ran: `run_case`'s own `emit(kind,
**data)` and `run_ladder`'s expected `on_event(kind, data_dict)` are
different call conventions — passing `emit` straight through would have
crashed the instant the ladder published anything; bridged with a small
lambda instead.

## "Hollywood" pass (implemented, v0)

`web/index.html` got a real production-value pass rather than staying a
functional data readout:

- **Real comic thought bubbles** — a cloud-shaped bubble with two trailing
  tail circles (pure CSS, `::before`/`::after`), not a plain dashed box.
- **A round table** — the two persona cards now sit inside an oval
  "table" surface (radial-gradient background) rather than plain
  side-by-side cards. Still 1v1 for now; the shape scales naturally once
  real multi-seat teams exist.
- **A billboard** — a scoreboard-style readout of the current standing
  claim, updating live as the ladder escalates, visible only to the human
  (the AI negotiators never see the DOM at all — this was already true
  architecturally, the billboard just makes it visually obvious).
- **A visual dice roll** — both hidden resolve values "roll" (rapid random
  flicker, ~650ms) before landing on their true value at the reveal,
  instead of just appearing as text.
- **A real reveal moment** — a tension pause (with a rising-pitch sound
  and pulsing ". . .") before the dice settle and the decision badges pop
  in with a spring animation, plus a brief full-screen color flash
  (green/red) timed to the outcome.
- **Sound**, entirely synthesized via the Web Audio API (oscillators +
  gain envelopes) — no audio files to source, store, or manage, staying
  zero-dependency like everything else in this project. Distinct tones
  for a raise, a call, an execution, and split vs. steal reveals.

Verified fully live in the browser, not just written: ran a complete live
case end to end (predict → debate → ladder with billboard updating in
sync → tension pause → dice landing on the actual true resolve values →
final reveal), confirmed zero console errors throughout, and directly
inspected the running page's JS state afterward (`audioCtx.state ===
"running"`, not `"suspended"` — confirming sound actually played rather
than being silently blocked by the browser's autoplay policy).

Known rough edge, cosmetic only: the ladder's displayed "reason" text is
currently just the live model's raw reply (`"RAISE_SMALL"`, `"CALL"`)
since the prompt only asks for the keyword — reads flat rather than
in-character. A follow-up prompt asking for a short justification
alongside the keyword is the fix, same shape as `real_intent`'s existing
verdict-then-reason pattern.

## Open questions — proposed defaults

Not fully settled, but defaulting to what already worked in Dominion rather
than re-litigating from scratch. Flagging each so any of them can be
overridden:

- **Team size — proposing 2 agents per team to start.** Simpler to build
  and to watch than a 3-agent caucus; matches Split or Steal's original
  2-person shape, just doubled into teams. Easy to grow later once the core
  loop is proven.
- **Model roster — proposing the same pool as Dominion**
  (llama3.2, qwen2.5:3b, gemma2:2b, phi3:mini). Negotiation is persuasive
  dialogue, which is exactly what small local models are already good at
  (see "Why this shape" above) — no evidence yet that this needs bigger,
  slower models. Worth revisiting only if debate quality actually turns out
  thin in practice.
- **Host implementation — proposing scripted-first, same as Dominion's
  `ScriptedAgent`-before-`OllamaAgent` pattern.** Rule-based narration keyed
  to the case's framework (Ultimatum/Trust/Stag Hunt/Chicken) is cheap,
  always available, and never stalls the show; a live model call per beat
  can layer on top later the same way Dominion added it, not as a
  day-one dependency.
- **Tech stack** — still proposing Dominion's own playbook (Python stdlib
  engine + local HTTP server + plain HTML/JS frontend, zero external
  deps) — proven, and nothing about this design needs anything heavier.
