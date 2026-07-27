"""The resolve/bluff escalation ladder -- the Liar's Dice-inspired mechanic
that runs before the final split/steal decision (engine/negotiation.py).

Structure: each side has a hidden 'resolve' value (1-10, rolled fresh per
case, like a die). Claims are about the COMBINED total (resolve_a +
resolve_b) -- neither side can ever be fully certain of the true total,
since each only knows their own number. That's the same "you know part of
the truth, not all of it" tension real Liar's Dice runs on, just with one
hidden number per side instead of a cup of five. Turns alternate: RAISE
(publicly claim the combined total is at least some higher number) or
CALL (challenge the standing claim, ending the ladder and revealing both
true resolve values).

Arithmetic stays in Python, never the model's job -- see probe_counting.py:
every locally-runnable model tested (four different families, up to 12B)
has a real, stubborn weakness at basic addition, independent of size. So
the model is only ever asked a QUALITATIVE question -- raise small, raise
big, or call -- never to state or validate a number itself. This module
computes every actual claim value and comparison.

Every live decision has a hard timeout (LADDER_TURN_TIMEOUT). On timeout,
or a reply that doesn't parse into a real move, the player is judged to
have frozen and loses the ladder outright -- 'executed' by their own
hesitation. This is a deliberate exception to how every OTHER live call in
this project degrades (OllamaAgent/OllamaNegotiator elsewhere always falls
back to a scripted default so the show never stalls) -- Scott's rule here
specifically: speed is part of the show, and a model that can't decide in
time doesn't get a quiet do-over, it pays for it in the game itself.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import random

from .cases import Case
from .personas import Persona

RESOLVE_MIN, RESOLVE_MAX = 1, 10
# Deliberately snappy -- Scott: "people get bored waiting for 'thinking'."
# Far tighter than OLLAMA_TIMEOUT (90s) elsewhere in this project, on
# purpose: this is a fast back-and-forth bluffing exchange, not a
# considered essay.
LADDER_TURN_TIMEOUT = 8.0
# Safety valve against a runaway ladder, same reasoning as duel.py's
# FORCED_PASS_MISS_STREAK -- if neither side calls, force one rather than
# let two agents raise forever.
MAX_RAISES = 10


class LadderTimeout(Exception):
    """Raised by a live negotiator's ladder_move when it fails to produce
    a valid move within LADDER_TURN_TIMEOUT -- caught by run_ladder, which
    turns it into an immediate loss for whoever's turn it was, not a
    graceful fallback. ScriptedNegotiator's own ladder_move is instant and
    deterministic and never raises this; only a live call can time out."""


def roll_resolve(rng: random.Random) -> int:
    return rng.randint(RESOLVE_MIN, RESOLVE_MAX)


@dataclass
class LadderResult:
    resolve_a: int
    resolve_b: int
    true_total: int
    standing_claim: int
    claimant_id: str            # "a" or "b" -- whoever made the claim that stood
    caller_id: str               # "a" or "b" -- whoever ended the ladder
    claim_was_true: bool         # true_total >= standing_claim
    winner_id: str               # "a" or "b"
    executed_id: Optional[str] = None  # set instead of a real call, if someone froze
    turns_log: List[Dict[str, Any]] = field(default_factory=list)


def run_ladder(persona_a: Persona, persona_b: Persona, case: Case,
               negotiator_a, negotiator_b, rng: random.Random,
               on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> LadderResult:
    def emit(kind: str, **data: Any) -> None:
        if on_event:
            on_event(kind, data)

    resolve_a = roll_resolve(rng)
    resolve_b = roll_resolve(rng)
    true_total = resolve_a + resolve_b
    # ladder_start deliberately does NOT carry true_total to on_event's
    # public consumers in a real show -- it's included on the RESULT
    # object for the human's own full-transparency view (design/DESIGN.md's
    # Transparency section), but a caller wiring this to anything
    # audience-facing should withhold it until the reveal.

    players = {"a": (persona_a, negotiator_a, resolve_a), "b": (persona_b, negotiator_b, resolve_b)}
    turns_log: List[Dict[str, Any]] = []
    current_claimant = "a"
    last_claimant_id: Optional[str] = None
    standing_claim = 0
    raises = 0

    while True:
        pid = current_claimant
        persona, negotiator, my_resolve = players[pid]
        opponent_id = "b" if pid == "a" else "a"
        opponent_persona = players[opponent_id][0]
        is_opening = standing_claim == 0

        try:
            move, reason = negotiator.ladder_move(
                persona, opponent_persona, standing_claim, my_resolve, case,
                is_opening=is_opening, timeout=LADDER_TURN_TIMEOUT)
        except LadderTimeout:
            winner_id = opponent_id
            emit("ladder_execution", player_id=pid)
            return LadderResult(resolve_a=resolve_a, resolve_b=resolve_b, true_total=true_total,
                                 standing_claim=standing_claim, claimant_id=pid, caller_id=opponent_id,
                                 claim_was_true=True, winner_id=winner_id, executed_id=pid,
                                 turns_log=turns_log)

        if move == "call" and not is_opening:
            claimant_id = last_claimant_id  # the standing claim belongs to the OTHER player
            caller_id = pid
            claim_was_true = true_total >= standing_claim
            winner_id = claimant_id if claim_was_true else caller_id
            turns_log.append({"player_id": pid, "move": "call", "reason": reason,
                               "standing_claim": standing_claim})
            emit("ladder_call", caller_id=caller_id, claimant_id=claimant_id,
                 standing_claim=standing_claim, reason=reason)
            return LadderResult(resolve_a=resolve_a, resolve_b=resolve_b, true_total=true_total,
                                 standing_claim=standing_claim, claimant_id=claimant_id,
                                 caller_id=caller_id, claim_was_true=claim_was_true,
                                 winner_id=winner_id, turns_log=turns_log)

        # A raise (or a forced raise, if a live move somehow returned
        # "call" on the opening turn when there's nothing to call yet).
        bump = {"raise_small": rng.randint(1, 2), "raise_big": rng.randint(3, 5)}.get(move, rng.randint(1, 2))
        new_claim = standing_claim + bump
        standing_claim = new_claim
        last_claimant_id = pid
        raises += 1
        turns_log.append({"player_id": pid, "move": move, "reason": reason, "claim": new_claim})
        emit("ladder_raise", player_id=pid, claim=new_claim, move=move, reason=reason)

        if raises >= MAX_RAISES:
            forced_caller = opponent_id
            claim_was_true = true_total >= standing_claim
            winner_id = pid if claim_was_true else forced_caller
            emit("ladder_forced_call", caller_id=forced_caller, claimant_id=pid)
            return LadderResult(resolve_a=resolve_a, resolve_b=resolve_b, true_total=true_total,
                                 standing_claim=standing_claim, claimant_id=pid,
                                 caller_id=forced_caller, claim_was_true=claim_was_true,
                                 winner_id=winner_id, turns_log=turns_log)

        current_claimant = opponent_id
