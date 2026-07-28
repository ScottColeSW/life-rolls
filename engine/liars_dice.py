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
import math
import random

from .cases import Case
from .personas import Persona

HAND_SIZE = 5    # dice per player at tournament start -- the standard Liar's Dice rule.
# Was 3 (a deliberate pacing shortcut) until Scott cross-checked this
# engine against researched real rules and asked to match the standard --
# a full tournament now needs a structural minimum of (players-1)*5 lost
# rounds before one winner remains, meaningfully more than at 3, so a
# full run naturally takes longer. Accepted tradeoff, not an oversight.
FACES = 6        # standard d6
WILD_FACE = 1    # 1s count toward any face bid, standard Liar's Dice rule
TURN_TIMEOUT = 8.0
PALAFOX_TRIGGER_DICE = 1  # a round opened by a player down to this many dice is a Palafox round -- see run_round


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
        # comparison. This holds for any raise that doesn't cross the
        # ace/non-ace boundary -- switching a bid onto or off of 1s uses
        # the separate halve/double-plus-one formulas in _valid_raise
        # instead (the "Bidding Aces" / Ace-Switch house rule), since a
        # bid on wild 1s is worth roughly double an ordinary face and
        # isn't comparable to one via this plain encoding.
        return self.quantity * (FACES + 1) + self.face


@dataclass
class PlayerState:
    persona: Persona
    negotiator: Any
    hand: List[int] = field(default_factory=list)
    alive: bool = True
    # Explicit, settable count -- NOT derived from len(hand). Before Spot
    # On, dice only ever shrank, so len(hand) was a safe proxy; Spot On
    # needs a die to come back after being lost, which means something has
    # to be able to increase, capped at HAND_SIZE. hand itself is still
    # just "this round's rolled dice," rebuilt fresh via _roll_hand(
    # dice_remaining, rng) at the start of every round.
    dice_remaining: int = HAND_SIZE

    @property
    def dice_count(self) -> int:
        return self.dice_remaining


@dataclass
class RoundResult:
    bid: Optional[Bid]
    claimant_index: Optional[int]
    caller_index: int
    actual_count: int
    bid_was_true: bool
    # None instead of an int when a Spot On call SUCCEEDS -- nobody loses a
    # die that round, someone gains one instead (see gained_die_index).
    # Every other outcome (a normal call, a failed Spot On, an execution)
    # still sets this normally.
    loser_index: Optional[int] = None
    # Set only when a Spot On call is exactly correct -- that player gets
    # a previously-lost die back (capped at HAND_SIZE), and nobody loses
    # one this round. Mutually exclusive with loser_index being set.
    gained_die_index: Optional[int] = None
    is_spot_on: bool = False
    # True only when actual_count == bid.quantity exactly -- distinct from
    # bid_was_true (the plain ">=" fact, still meaningful for narration
    # even on a Spot On call, e.g. "the real count was even higher").
    spot_on_correct: Optional[bool] = None
    executed_index: Optional[int] = None
    # True when this round was opened by a player down to their last die --
    # no wild ones for anyone this round, and that player's opening bid was
    # forced (their true die value, no agency). See run_round.
    is_palafox: bool = False
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


def _count_matching(hands: List[List[int]], face: int, wild_active: bool = True) -> int:
    count = 0
    for hand in hands:
        for d in hand:
            if d == face or (wild_active and face != WILD_FACE and d == WILD_FACE):
                count += 1
    return count


def _valid_raise(new: Bid, old: Optional[Bid], wild_active: bool = True) -> bool:
    if old is None:
        return 1 <= new.face <= FACES and new.quantity >= 1
    # Bidding Aces / "Ace-Switch": crossing the ace/non-ace boundary uses
    # its own halve-or-double-plus-one formulas instead of the plain
    # ordinal comparison, since a bid on wild 1s is worth roughly double
    # an ordinary face (every 1 already counts toward any face bid) -- the
    # house rule Scott researched and asked for, previously deliberately
    # left unimplemented (see Bid.value()'s docstring) as a first-version
    # simplification. Only applies when wild_active -- during a Palafox
    # round (see run_round) 1s aren't wild at all, so face 1 is just the
    # lowest ordinary face and the plain ordinal comparison already
    # handles it correctly with no special case needed.
    if wild_active:
        if old.face != WILD_FACE and new.face == WILD_FACE:
            return new.quantity >= math.ceil(old.quantity / 2)
        if old.face == WILD_FACE and new.face != WILD_FACE:
            return new.quantity >= old.quantity * 2 + 1
    return new.value() > old.value()


def compute_opening_bid(hand: List[int]) -> Bid:
    counts = Counter(hand)
    wilds = counts.get(WILD_FACE, 0)
    best_face = max(range(2, FACES + 1), key=lambda f: counts.get(f, 0) + wilds)
    qty = max(1, counts.get(best_face, 0) + wilds)
    return Bid(quantity=qty, face=best_face)


def compute_same_face_raise(standing: Bid) -> Bid:
    return Bid(quantity=standing.quantity + 1, face=standing.face)


def compute_ace_switch_raise(standing: Bid) -> Bid:
    """The minimum legal bid when switching ONTO wild aces -- roughly half
    (rounded up) of the standing quantity, per the Ace-Switch formula.
    Never called when standing is already on aces (compute_same_face_raise
    covers staying on 1s -- an ordinary quantity+1 bump, no halving needed
    since the face doesn't change)."""
    return Bid(quantity=max(1, math.ceil(standing.quantity / 2)), face=WILD_FACE)


def compute_new_face_raise(hand: List[int], standing: Bid, wild_active: bool = True) -> Bid:
    counts = Counter(hand)
    wilds = counts.get(WILD_FACE, 0) if wild_active else 0
    if wild_active and standing.face == WILD_FACE:
        # Switching AWAY from aces uses the Ace-Switch "give-back" formula
        # (double the ace quantity, plus one) -- not the ordinary
        # same-or-next-face bump used between two non-ace faces, and never
        # itself proposes switching to a DIFFERENT ace bid (there's only
        # one ace face). Skipped entirely when wild_active is False --
        # during a Palafox round face 1 was never wild to begin with, so
        # there's no doubling debt to give back; it falls through to the
        # ordinary candidates logic below instead.
        min_qty = standing.quantity * 2 + 1
        best_face = max(range(2, FACES + 1), key=lambda f: counts.get(f, 0) + wilds)
        return Bid(quantity=min_qty, face=best_face)
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
    # Palafox: a round opened by a player down to their last die is special
    # -- no wild ones for ANYONE this round (a real calculation-hardening
    # rule researched and requested by Scott), and that player's opening
    # bid is forced to their own die's true value, no agency at all. This
    # is decided once, up front, from dice_remaining alone (known before
    # any hand is even rolled) -- it stays fixed for the whole round even
    # if wild_active ends up mattering again on a later round.
    is_palafox = players[opener_idx].dice_count == PALAFOX_TRIGGER_DICE
    wild_active = not is_palafox
    for p in players:
        if p.alive:
            p.hand = _roll_hand(p.dice_count, rng)
    # Every real hand goes out AS SOON AS it's rolled, not just at the
    # end-of-round reveal -- the human running the show sees everything,
    # same Transparency principle as RoundResult.hands, just surfaced
    # earlier (Scott: "I'd rather we see them all the time; before,
    # during, and after each roll... I need to see the choices and
    # bluffs more visually").
    hands_snapshot = {i: list(players[i].hand) for i in alive_indices}
    emit("round_start", alive_indices=list(alive_indices), is_palafox=is_palafox, hands=hands_snapshot)

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

        if is_palafox and standing_bid is None and current_idx == opener_idx:
            # Forced, non-negotiable opening -- the Palafox player has only
            # one die and must reveal its true value as the opening bid.
            # Bypasses the negotiator entirely: there's no decision to make
            # (and nothing to bluff about) when your whole hand is one die.
            reason = "Forced Palafox opening -- my last die, exactly as it lies."
            forced_bid = Bid(quantity=1, face=player.hand[0])
            standing_bid = forced_bid
            claimant_idx = current_idx
            turns_log.append({"player_index": current_idx, "move": "raise", "bid": forced_bid, "reason": reason})
            emit("round_raise", player_index=current_idx, bid=forced_bid, reason=reason, is_palafox_open=True)
            turn_pos += 1
            continue

        # Fired right before the negotiator is actually asked for a move --
        # purely informational, no effect on game logic. Lets the client
        # show a "thinking" indicator on this seat for however long a real
        # live Ollama round-trip actually takes; a scripted negotiator
        # resolves instantly so this is a no-op visually for it either way.
        emit("player_thinking", player_index=current_idx)
        try:
            move, bid, reason = player.negotiator.dice_move(
                player.persona, player.hand, standing_bid, total_other_dice, case, timeout=TURN_TIMEOUT,
                wild_active=wild_active)
        except RoundTimeout:
            snapshot = {i: list(players[i].hand) for i in alive_indices}
            emit("round_execution", player_index=current_idx)
            return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                actual_count=-1, bid_was_true=False, loser_index=current_idx,
                                executed_index=current_idx, is_palafox=is_palafox,
                                turns_log=turns_log, hands=snapshot)

        if move == "call" and standing_bid is not None:
            hands_list = [players[i].hand for i in alive_indices]
            actual = _count_matching(hands_list, standing_bid.face, wild_active=wild_active)
            bid_was_true = actual >= standing_bid.quantity
            # If the bid was true, the CALLER wrongly doubted an honest
            # claim and loses; if it was a bluff, the CLAIMANT got caught
            # lying and loses. This was previously backwards -- copied the
            # shape of resolve_ladder.py's winner_id = claimant_id if
            # claim_was_true else caller_id (correct there, since that
            # computes the WINNER) without flipping the branches for
            # computing the LOSER instead, silently rewarding bluffing and
            # punishing honest calls in every tournament run before this fix.
            loser_idx = current_idx if bid_was_true else claimant_idx
            snapshot = {i: list(players[i].hand) for i in alive_indices}
            turns_log.append({"player_index": current_idx, "move": "call", "reason": reason})
            # hands=snapshot included here (not just on the returned
            # RoundResult below) -- a live streaming consumer needs the
            # honest reveal data in the event itself, in real time, not
            # just in a final result object it may never see if it's
            # only subscribed to the live event stream.
            emit("round_call", caller_index=current_idx, claimant_index=claimant_idx,
                 bid=standing_bid, actual_count=actual, bid_was_true=bid_was_true, hands=snapshot)
            return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                actual_count=actual, bid_was_true=bid_was_true, loser_index=loser_idx,
                                is_palafox=is_palafox, turns_log=turns_log, hands=snapshot)

        if move == "spot_on" and standing_bid is not None:
            # A correct Spot On is a MORE severe version of a correct call
            # -- the claimant is caught out exactly as they would be by a
            # normal successful call (they lose a die), PLUS the caller is
            # separately rewarded for the precision with a die back. Both
            # happen together on success, not just the caller's gain alone
            # -- confirmed by Scott's own worked example ("Player 4 is
            # penalized, AND you get to reclaim a previously lost die"),
            # which corrected an earlier assumption here that only the
            # caller was ever affected either way. A WRONG Spot On (high or
            # low) only costs the caller -- the claimant is never penalized
            # twice over for one bid.
            hands_list = [players[i].hand for i in alive_indices]
            actual = _count_matching(hands_list, standing_bid.face, wild_active=wild_active)
            spot_on_correct = actual == standing_bid.quantity
            snapshot = {i: list(players[i].hand) for i in alive_indices}
            turns_log.append({"player_index": current_idx, "move": "spot_on", "reason": reason})
            emit("round_spot_on", caller_index=current_idx, claimant_index=claimant_idx,
                 bid=standing_bid, actual_count=actual, spot_on_correct=spot_on_correct, hands=snapshot)
            if spot_on_correct:
                return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                    actual_count=actual, bid_was_true=True, is_spot_on=True,
                                    spot_on_correct=True, loser_index=claimant_idx,
                                    gained_die_index=current_idx, is_palafox=is_palafox,
                                    turns_log=turns_log, hands=snapshot)
            return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                actual_count=actual, bid_was_true=(actual >= standing_bid.quantity),
                                is_spot_on=True, spot_on_correct=False, loser_index=current_idx,
                                is_palafox=is_palafox, turns_log=turns_log, hands=snapshot)

        if bid is None or not _valid_raise(bid, standing_bid, wild_active=wild_active):
            # Engine-side safety net: a live model's bid didn't actually
            # beat the standing one -- the same arithmetic slip
            # probe_counting.py already found, now caught here rather
            # than silently accepted or crashing. Treated the same as a
            # freeze: executed on the spot.
            snapshot = {i: list(players[i].hand) for i in alive_indices}
            emit("round_execution", player_index=current_idx)
            return RoundResult(bid=standing_bid, claimant_index=claimant_idx, caller_index=current_idx,
                                actual_count=-1, bid_was_true=False, loser_index=current_idx,
                                executed_index=current_idx, is_palafox=is_palafox,
                                turns_log=turns_log, hands=snapshot)

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

    players = [PlayerState(persona=p, negotiator=neg, hand=_roll_hand(HAND_SIZE, rng), dice_remaining=HAND_SIZE)
               for p, neg in zip(personas, negotiators)]
    order_of_elimination: List[int] = []
    rounds: List[RoundResult] = []
    opener = 0

    while sum(1 for p in players if p.alive) > 1:
        result = run_round(players, opener, case, rng, on_event=on_event)
        rounds.append(result)

        # A successful Spot On call moves a die BOTH ways in the same
        # round -- the claimant loses one (loser_index, handled below like
        # any other loss) AND the caller separately gains one back,
        # capped at HAND_SIZE so a Spot On streak can't grow a hand past
        # where it started.
        if result.gained_die_index is not None:
            gainer = players[result.gained_die_index]
            gainer.dice_remaining = min(HAND_SIZE, gainer.dice_remaining + 1)
            emit("player_gained_die", player_index=result.gained_die_index,
                 dice_remaining=gainer.dice_remaining)

        loser = players[result.loser_index]
        loser.dice_remaining -= 1
        if loser.dice_remaining <= 0:
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
