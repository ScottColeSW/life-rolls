"""Authentic multi-round Liar's Dice tournament -- the actual resolution
mechanic for who wins the pot, replacing the old 2-party resolve ladder +
split/steal decision (engine/resolve_ladder.py, now superseded in the main
flow by this). Real quantity+face bids ("4 fives"), 1s wild, round-robin
turn order among however many players are seated, elimination on a lost
round, last player standing takes the whole pot.

Arithmetic -- counting matching dice, validating a raise, comparing bids
-- stays entirely in Python; the model is only ever asked a qualitative
question (raise on the same face, switch to a new face, or call). See
probe_counting.py: every locally-runnable model tested (four families, up
to 12B) has a real, stubborn weakness at basic arithmetic, independent of
size, so it never gets asked to state or validate a number itself.

Every live decision has a hard timeout (TURN_TIMEOUT). A model that
doesn't respond in time, or produces something that doesn't parse into a
real move, is judged to have frozen -- 'executed' outright: full,
immediate elimination from the tournament, not just losing a single die.
Same deliberate non-graceful-fallback exception as the old resolve ladder
(Scott: "people get bored waiting... execution if they fail to play in
time"). An engine-side illegal bid (a live model's raise that doesn't
actually beat the standing one -- the same arithmetic slip
probe_counting.py already found) is treated the same way: executed, not
silently corrected or rejected-and-retried.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import random

from .cases import Case
from .personas import Persona

HAND_SIZE = 3    # dice per player at tournament start -- smaller than the classic 5, on purpose, for pace
FACES = 6        # standard d6
WILD_FACE = 1    # 1s count toward any face bid, standard Liar's Dice rule
TURN_TIMEOUT = 8.0


class RoundTimeout(Exception):
    """A live player's turn didn't produce a valid, legal move in time --
    caught by run_tournament/run_round, which eliminates that player
    outright rather than falling back gracefully. Never raised by a
    scripted player, which is instant and deterministic."""


@dataclass(frozen=True)
class Bid:
    quantity: int
    face: int

    def value(self) -> int:
        # A simple strictly-increasing total order: raising EITHER the
        # quantity or the face produces a higher encoded value, so "any
        # raise is valid as long as it's higher" reduces to one integer
        # comparison. Deliberately NOT implementing the classic
        # halve-when-switching-to-1s house rule some Liar's Dice variants
        # use -- a documented simplification for a first working version,
        # not a bug.
        return self.quantity * (FACES + 1) + self.face


@dataclass
class PlayerState:
    persona: Persona
    negotiator: Any
    hand: List[int] = field(default_factory=list)
    alive: bool = True

    @property
    def dice_count(self) -> int:
        return len(self.hand)


@dataclass
class RoundResult:
    bid: Optional[Bid]
    claimant_index: Optional[int]
    caller_index: int
    actual_count: int
    bid_was_true: bool
    loser_index: int
    executed_index: Optional[int] = None
    turns_log: List[Dict[str, Any]] = field(default_factory=list)
    # Every alive player's real hand at the moment this round ended --
    # keyed by player index, string keys once JSON-serialized. Needed for
    # an honest reveal animation (the human running the show sees
    # everything, same Transparency principle as the rest of this
    # project); not present on a round that's still in progress.
    hands: Dict[int, List[int]] = field(default_factory=dict)


@dataclass
class TournamentResult:
    winner_index: int
    order_of_elimination: List[int] = field(default_factory=list)
    rounds: List[RoundResult] = field(default_factory=list)


def _roll_hand(n: int, rng: random.Random) -> List[int]:
    return [rng.randint(1, FACES) for _ in range(n)]


def _count_matching(hands: List[List[int]], face: int) -> int:
    count = 0
    for hand in hands:
        for d in hand:
            if d == face or (face != WILD_FACE and d == WILD_FACE):
                count += 1
    return count


def _valid_raise(new: Bid, old: Optional[Bid]) -> bool:
    if old is None:
        return 1 <= new.face <= FACES and new.quantity >= 1
    return new.value() > old.value()


def compute_opening_bid(hand: List[int]) -> Bid:
    counts = Counter(hand)
    wilds = counts.get(WILD_FACE, 0)
    best_face = max(range(2, FACES + 1), key=lambda f: counts.get(f, 0) + wilds)
    qty = max(1, counts.get(best_face, 0) + wilds)
    return Bid(quantity=qty, face=best_face)


def compute_same_face_raise(standing: Bid) -> Bid:
    return Bid(quantity=standing.quantity + 1, face=standing.face)


def compute_new_face_raise(hand: List[int], standing: Bid) -> Bid:
    counts = Counter(hand)
    wilds = counts.get(WILD_FACE, 0)
    candidates = list(range(standing.face + 1, FACES + 1)) or [FACES]
    best_face = max(candidates, key=lambda f: counts.get(f, 0) + (wilds if f != WILD_FACE else 0))
    qty = standing.quantity
    bid = Bid(quantity=qty, face=best_face)
    if bid.value() <= standing.value():
        bid = Bid(quantity=qty + 1, face=best_face)
    return bid


def run_round(players: List[PlayerState], opener_idx: int, case: Case, rng: random.Random,
              on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> RoundResult:
    def emit(kind: str, **data: Any) -> None:
        if on_event:
            on_event(kind, data)

    alive_indices = [i for i, p in enumerate(players) if p.alive]
    for p in players:
        if p.alive:
            p.hand = _roll_hand(p.dice_count, rng)
    emit("round_start", alive_indices=list(alive_indices))

    order: List[int] = []
    n = len(players)
    idx = opener_idx
    for _ in range(n):
        if players[idx].alive:
            order.append(idx)
        idx = (idx + 1) % n

    standing_bid: Optional[Bid] = None
    claimant_idx: Optional[int] = None
    turns_log: List[Dict[str, Any]] = []
    turn_pos = 0

    while True:
        current_idx = order[turn_pos % len(order)]
        player = players[current_idx]
        total_other_dice = sum(players[i].dice_count for i in alive_indices if i != current_idx)

        try:
            move, bid, reason = player.negotiator.dice_move(
                player.persona, player.hand, standing_bid, total_other_dice, case, timeout=TURN_TIMEOUT)
        except RoundTimeout:
            snapshot = {i: list(players[i].hand) for i in alive_indices}
            emit("round_execution", player_index=current_idx)
            return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                actual_count=-1, bid_was_true=False, loser_index=current_idx,
                                executed_index=current_idx, turns_log=turns_log, hands=snapshot)

        if move == "call" and standing_bid is not None:
            hands_list = [players[i].hand for i in alive_indices]
            actual = _count_matching(hands_list, standing_bid.face)
            bid_was_true = actual >= standing_bid.quantity
            loser_idx = claimant_idx if bid_was_true else current_idx
            snapshot = {i: list(players[i].hand) for i in alive_indices}
            turns_log.append({"player_index": current_idx, "move": "call", "reason": reason})
            emit("round_call", caller_index=current_idx, claimant_index=claimant_idx,
                 bid=standing_bid, actual_count=actual, bid_was_true=bid_was_true)
            return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                actual_count=actual, bid_was_true=bid_was_true, loser_index=loser_idx,
                                turns_log=turns_log, hands=snapshot)

        if bid is None or not _valid_raise(bid, standing_bid):
            # Engine-side safety net: a live model's bid didn't actually
            # beat the standing one -- the same arithmetic slip
            # probe_counting.py already found, now caught here rather
            # than silently accepted or crashing. Treated the same as a
            # freeze: executed on the spot.
            snapshot = {i: list(players[i].hand) for i in alive_indices}
            emit("round_execution", player_index=current_idx)
            return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                actual_count=-1, bid_was_true=False, loser_index=current_idx,
                                executed_index=current_idx, turns_log=turns_log, hands=snapshot)

        standing_bid = bid
        claimant_idx = current_idx
        turns_log.append({"player_index": current_idx, "move": "raise", "bid": bid, "reason": reason})
        emit("round_raise", player_index=current_idx, bid=bid, reason=reason)
        turn_pos += 1


def run_tournament(personas: List[Persona], negotiators: List[Any], case: Case, rng: random.Random,
                    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> TournamentResult:
    def emit(kind: str, **data: Any) -> None:
        if on_event:
            on_event(kind, data)

    players = [PlayerState(persona=p, negotiator=neg, hand=_roll_hand(HAND_SIZE, rng))
               for p, neg in zip(personas, negotiators)]
    order_of_elimination: List[int] = []
    rounds: List[RoundResult] = []
    opener = 0

    while sum(1 for p in players if p.alive) > 1:
        result = run_round(players, opener, case, rng, on_event=on_event)
        rounds.append(result)
        loser = players[result.loser_index]
        loser.hand = loser.hand[:-1]
        if not loser.hand:
            loser.alive = False
            order_of_elimination.append(result.loser_index)
            emit("player_eliminated", player_index=result.loser_index)
            nxt = (result.loser_index + 1) % len(players)
            while not players[nxt].alive:
                nxt = (nxt + 1) % len(players)
            opener = nxt
        else:
            opener = result.loser_index

    winner_index = next(i for i, p in enumerate(players) if p.alive)
    emit("tournament_winner", player_index=winner_index)
    return TournamentResult(winner_index=winner_index, order_of_elimination=order_of_elimination, rounds=rounds)
