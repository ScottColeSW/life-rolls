"""Standalone verification for the Palafox/Showdown mechanic in
engine/liars_dice.py -- same approach as verify_spot_on.py and
verify_ace_switch.py: hand-computed expectations checked against the real
engine functions. Run: python verify_palafox.py
"""
from __future__ import annotations
import random
from typing import List, Optional, Tuple

from engine.liars_dice import (
    Bid, HAND_SIZE, PlayerState, WILD_FACE, _count_matching, _valid_raise, run_round,
)
from engine.personas import Persona

failures = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures
    status = "OK" if cond else "FAIL"
    if not cond:
        failures += 1
    print(f"  [{status}] {label} {detail}")


# --- _count_matching: wild_active=False disables the wild bonus ---------
print("--- No wild ones when wild_active=False ---")
hands = [[1, 4, 2], [1, 1, 5]]
# One literal 4 (hand 1) + three wild 1s (one in hand 1, two in hand 2) = 4.
check("with wilds active, every 1 counts toward face 4 too",
      _count_matching(hands, 4, wild_active=True) == 4, f"got {_count_matching(hands, 4, wild_active=True)}")
check("with wilds OFF, only literal 4s count",
      _count_matching(hands, 4, wild_active=False) == 1, f"got {_count_matching(hands, 4, wild_active=False)}")
check("a bid ON face 1 itself is unaffected either way (literal 1s always count as 1s)",
      _count_matching(hands, 1, wild_active=True) == _count_matching(hands, 1, wild_active=False) == 3)

# --- _valid_raise: no Ace-Switch formulas when wild_active=False ---------
print("--- No Ace-Switch halving/doubling when wild_active=False ---")
standing = Bid(quantity=7, face=4)
check("switching to face 1 at quantity 4 (the Ace-Switch minimum) is ILLEGAL "
      "when wilds are off -- plain ordinal comparison applies instead",
      not _valid_raise(Bid(quantity=4, face=WILD_FACE), standing, wild_active=False))
check("switching to face 1 needs the ordinary quantity+ordinal beat when wilds are off",
      _valid_raise(Bid(quantity=8, face=WILD_FACE), standing, wild_active=False))
standing_ace = Bid(quantity=3, face=WILD_FACE)
check("leaving face 1 at quantity 4 (below the Ace-Switch double+1 minimum of 7) "
      "is LEGAL when wilds are off -- 1 is just an ordinary weak face",
      _valid_raise(Bid(quantity=4, face=2), standing_ace, wild_active=False))
check("the same bid would be illegal with wilds ON (needs 3*2+1=7)",
      not _valid_raise(Bid(quantity=4, face=2), standing_ace, wild_active=True))


# --- run_round: a Palafox round is correctly detected and forces the open ---
print("--- run_round: Palafox detection + forced opening ---")


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


events: List[Tuple[str, dict]] = []


def capture(kind, data):
    events.append((kind, data))


# P0 is the Palafox player -- exactly 1 die remaining, rolls a 4. P1 has a
# normal 5-die hand. P0's opening bid should be forced to Bid(1, 4) without
# ever consulting its negotiator (moves=[] -- if the engine tried to call
# it, this would raise IndexError and fail loudly).
p0 = PlayerState(persona=make_persona("P0"), negotiator=FixedMoveNegotiator([]),
                  hand=[], alive=True, dice_remaining=1)
p1 = PlayerState(persona=make_persona("P1"),
                  negotiator=FixedMoveNegotiator([("call", None, "not buying it")]),
                  hand=[], alive=True, dice_remaining=5)
rng = ScriptedRng([4] + [2, 2, 2, 2, 2])  # P0's single die = 4; P1's hand = five 2s
events.clear()
result = run_round([p0, p1], opener_idx=0, case=None, rng=rng, on_event=capture)

check("RoundResult.is_palafox is True", result.is_palafox is True)
check("standing bid at the time of the call is P0's forced opening", result.claimant_index == 0)
round_start_events = [d for k, d in events if k == "round_start"]
round_raise_events = [d for k, d in events if k == "round_raise"]
check("round_start event carries is_palafox=True", round_start_events[0]["is_palafox"] is True)
check("round_raise event for the forced open carries is_palafox_open=True",
      round_raise_events[0].get("is_palafox_open") is True)
check("the forced open's bid is (1, 4) -- P0's actual rolled die, not a negotiator choice",
      round_raise_events[0]["bid"] == Bid(quantity=1, face=4))

# The call: standing bid is (1, 4). P1's hand is five 2s -- with wilds OFF
# (Palafox), actual count of 4s across both hands is 1 (P0's own die) + 0
# (P1 has no 4s and no wild 1s to help) = 1, which meets the bid of 1
# exactly -- bid_was_true should be True, so P1 (the caller) loses.
check("actual_count for the call is 1 (no wild bonus from anyone)", result.actual_count == 1,
      f"got {result.actual_count}")
check("bid_was_true is True (1 real 4 >= claimed 1)", result.bid_was_true is True)
check("P1 (the caller) is the loser, since the forced bid held up", result.loser_index == 1,
      f"got {result.loser_index}")

# --- Contrast: an ordinary round (opener has more than 1 die) is NOT Palafox
print("--- run_round: an ordinary round is not mistaken for Palafox ---")
p0n = PlayerState(persona=make_persona("P0"),
                   negotiator=FixedMoveNegotiator([("raise", Bid(quantity=2, face=3), "opening")]),
                   hand=[], alive=True, dice_remaining=5)
p1n = PlayerState(persona=make_persona("P1"),
                   negotiator=FixedMoveNegotiator([("call", None, "not buying it")]),
                   hand=[], alive=True, dice_remaining=5)
rng_n = ScriptedRng([1, 2, 3, 4, 5] + [1, 2, 3, 4, 5])
result_n = run_round([p0n, p1n], opener_idx=0, case=None, rng=rng_n)
check("a round opened by a player with 5 dice is NOT Palafox", result_n.is_palafox is False)

print()
if failures:
    print(f"{failures} check(s) FAILED")
else:
    print("All checks passed.")
