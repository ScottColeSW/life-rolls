"""Standalone verification for the Bidding Aces / Ace-Switch mechanic in
engine/liars_dice.py -- same approach as verify_spot_on.py: hand-computed
expectations checked against the real engine functions, not a fixture
replay. Run: python verify_ace_switch.py
"""
from __future__ import annotations
import math
import random
from typing import List, Optional, Tuple

from engine.liars_dice import (
    Bid, HAND_SIZE, PlayerState, RoundTimeout, WILD_FACE, _valid_raise,
    compute_ace_switch_raise, compute_new_face_raise, run_round,
)
from engine.personas import Persona

failures = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures
    status = "OK" if cond else "FAIL"
    if not cond:
        failures += 1
    print(f"  [{status}] {label} {detail}")


# --- _valid_raise: switching ONTO aces -----------------------------------
print("--- Switching onto aces (halve, round up) ---")
standing = Bid(quantity=7, face=4)
min_qty = math.ceil(7 / 2)  # 4
check("min ace quantity is ceil(7/2) = 4", min_qty == 4)
check("exactly the minimum is legal", _valid_raise(Bid(quantity=4, face=WILD_FACE), standing))
check("one below the minimum is illegal", not _valid_raise(Bid(quantity=3, face=WILD_FACE), standing))
check("above the minimum is legal", _valid_raise(Bid(quantity=5, face=WILD_FACE), standing))
check("compute_ace_switch_raise matches the same minimum",
      compute_ace_switch_raise(standing) == Bid(quantity=4, face=WILD_FACE))

standing_odd = Bid(quantity=5, face=2)
check("odd quantity rounds UP: ceil(5/2) = 3 is legal",
      _valid_raise(Bid(quantity=3, face=WILD_FACE), standing_odd))
check("odd quantity rounds UP: 2 is NOT enough", not _valid_raise(Bid(quantity=2, face=WILD_FACE), standing_odd))

# --- _valid_raise: switching AWAY from aces ------------------------------
print("--- Switching away from aces (double, plus one) ---")
standing_ace = Bid(quantity=4, face=WILD_FACE)
required = 4 * 2 + 1  # 9
check("min quantity after doubling+1 is 9", required == 9)
check("exactly 9 is legal", _valid_raise(Bid(quantity=9, face=5), standing_ace))
check("8 (one below) is illegal", not _valid_raise(Bid(quantity=8, face=5), standing_ace))
check("10 (above minimum) is legal", _valid_raise(Bid(quantity=10, face=3), standing_ace))
new_bid = compute_new_face_raise(hand=[3, 3, 5, 2, 6], standing=standing_ace)
check("compute_new_face_raise applies the doubling+1 minimum when leaving aces",
      new_bid.quantity == 9, f"got {new_bid.quantity}")
check("compute_new_face_raise never proposes face==1 when leaving aces", new_bid.face != WILD_FACE)

# --- Same-face raises are unaffected by the Ace-Switch formulas ---------
print("--- Ordinary same-face raises (no ace boundary crossed) ---")
check("staying on aces still just needs quantity+1",
      _valid_raise(Bid(quantity=5, face=WILD_FACE), Bid(quantity=4, face=WILD_FACE)))
check("staying on aces at the SAME quantity is still illegal",
      not _valid_raise(Bid(quantity=4, face=WILD_FACE), Bid(quantity=4, face=WILD_FACE)))
check("ordinary face-to-face raise unaffected: quantity+1 same face is legal",
      _valid_raise(Bid(quantity=5, face=3), Bid(quantity=4, face=3)))
check("ordinary face-to-face raise unaffected: same quantity, lower face is illegal",
      not _valid_raise(Bid(quantity=4, face=2), Bid(quantity=4, face=3)))


# --- Engine-level integration: an illegal Ace-Switch bid gets executed ---
print("--- run_round: an illegal Ace-Switch bid is caught by the safety net ---")


class ScriptedRng(random.Random):
    def __init__(self, sequence: List[int]):
        super().__init__()
        self._seq = list(sequence)
        self._i = 0

    def randint(self, a: int, b: int) -> int:
        if self._i < len(self._seq):
            v = self._seq[self._i]
            self._i += 1
            return v
        return super().randint(a, b)


class FixedMoveNegotiator:
    def __init__(self, moves: List[Tuple[str, Optional[Bid], str]]):
        self.moves = list(moves)
        self.calls = 0

    def dice_move(self, player, hand, standing_bid, total_other_dice, case, timeout=8.0, wild_active=True):
        move = self.moves[self.calls]
        self.calls += 1
        return move


def make_persona(name: str) -> Persona:
    return Persona(archetype_name=name, mandate="test", private_incentive="test",
                    risk_tolerance=0.5, trust_propensity=0.5)


# P0 opens bid(7,4). P1 tries to switch to aces at quantity 3 -- one below
# the required ceil(7/2)=4 -- an illegal raise, same category as a live
# model's arithmetic slip: the engine should execute P1 on the spot rather
# than accept or silently correct it.
p0 = PlayerState(persona=make_persona("P0"),
                  negotiator=FixedMoveNegotiator([("raise", Bid(quantity=7, face=4), "opening")]),
                  hand=[4, 4, 2, 3, 5], alive=True, dice_remaining=5)
p1 = PlayerState(persona=make_persona("P1"),
                  negotiator=FixedMoveNegotiator([("raise", Bid(quantity=3, face=WILD_FACE), "illegal switch")]),
                  hand=[1, 2, 3, 4, 6], alive=True, dice_remaining=5)
rng = ScriptedRng(p0.hand + p1.hand)
result = run_round([p0, p1], opener_idx=0, case=None, rng=rng)
check("illegal Ace-Switch bid triggers execution, not silent correction",
      result.executed_index == 1, f"got executed_index={result.executed_index}")
check("the executed player (P1) is the one who loses a die",
      result.loser_index == 1, f"got {result.loser_index}")

# Same setup, but P1's switch is exactly at the legal minimum (4) -- should
# be accepted as an ordinary raise, continuing the round instead of ending it.
p0b = PlayerState(persona=make_persona("P0"),
                   negotiator=FixedMoveNegotiator([("raise", Bid(quantity=7, face=4), "opening"),
                                                    ("call", None, "not buying it")]),
                   hand=[4, 4, 2, 3, 5], alive=True, dice_remaining=5)
p1b = PlayerState(persona=make_persona("P1"),
                   negotiator=FixedMoveNegotiator([("raise", Bid(quantity=4, face=WILD_FACE), "legal switch")]),
                   hand=[1, 2, 3, 4, 6], alive=True, dice_remaining=5)
rng_b = ScriptedRng(p0b.hand + p1b.hand)
result_b = run_round([p0b, p1b], opener_idx=0, case=None, rng=rng_b)
check("a legal Ace-Switch bid is accepted (no execution)", result_b.executed_index is None,
      f"got executed_index={result_b.executed_index}")
check("the round continues normally to a call", result_b.bid == Bid(quantity=4, face=WILD_FACE))

print()
if failures:
    print(f"{failures} check(s) FAILED")
else:
    print("All checks passed.")
