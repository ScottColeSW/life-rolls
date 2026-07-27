# Life Rolls — Design Doc (v1: Liar's Dice)

**This project is Life Rolls now, not Split Decision.** The pot/case/
split-steal framing below (kept for its own historical record) was the
earlier idea; a real, authentic multi-round Liar's Dice tournament —
quantity+face bids, round-robin among 5+ seated players, elimination on a
lost round, last one standing takes the whole pot — is the actual game.
See "Liar's Dice tournament (implemented)" further down for the current
mechanic. Renaming the repo folder, page titles, and file names to match
is real cleanup still owed, deliberately not done yet so it doesn't
happen mid-rebuild.

## Liar's Dice tournament (implemented, replaces the old resolve ladder + split/steal)

`engine/liars_dice.py` (`Bid`, `PlayerState`, `RoundResult`,
`TournamentResult`, `run_round`, `run_tournament`), plus `dice_move` on
both negotiator classes in `engine/negotiation.py`. Proven via
`dice_demo.py` (`--seed N`, `--players N`, `--live`, `--force-timeout`).

- Each of N seated players (5+) gets a hand of dice — `HAND_SIZE = 3`
  (not the classic 5) deliberately, for pace: fewer dice per player means
  faster rounds and less to track visually. Easy to raise later.
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
for N players, not just 2 fixed sides), real dice-face glyphs (⚀-⚅), a
billboard tracking the live standing bid, turn highlighting, an honest
dice reveal (actual hand values, not just the computed count) using
`RoundResult.hands`, and elimination/winner visuals. `POST
/api/run-tournament?players=N&live=0|1` on the server side. Currently
precompute-then-replay (the server runs the whole tournament, including
every live Ollama call, before responding; the browser replays the
already-finished result at a tuned pace) — verified end to end via direct
JS execution (bypassing click-coordinate flakiness in this session's
browser tool), including real round-table geometry and a full elimination
sequence with zero console errors.

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
