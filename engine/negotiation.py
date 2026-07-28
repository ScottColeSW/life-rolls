"""Negotiation engine: the case / gut-read / debate / real-intent / decide /
reveal loop.

v0 scope: one negotiator per side, not the full 2-agent teams-with-caucus
from design/DESIGN.md -- a deliberate scope cut to get the actual loop
running and watchable first. Teams are the natural next step once this
shape is proven.

Scripted-first, same safety pattern as Dominion: ScriptedNegotiator gives
every persona a reliable, dependency-free way to debate and decide, no
Ollama required. OllamaNegotiator (optional) upgrades this to a live local
model, falling back to the exact same scripted behavior on any failure --
timeout, connection error, unparseable reply -- so a slow or unreachable
Ollama server can never stall a show. Mirrors Dominion's
engine/ollama_agent.py, including the 127.0.0.1-not-localhost fix (Ollama
only listens on IPv4; "localhost" can resolve to the IPv6 loopback first
and hang instead of failing fast).
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Callable
import json
import random
import urllib.error
import urllib.request

from .cases import Case
from .personas import Persona
from .resolve_ladder import LadderResult, LadderTimeout, run_ladder
from .liars_dice import (
    HAND_SIZE, WILD_FACE, Bid, RoundTimeout, compute_ace_switch_raise, compute_new_face_raise,
    compute_opening_bid, compute_same_face_raise,
)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TIMEOUT = 20.0
# qwen2.5:7b added per probe_counting.py's results: ties the top accuracy
# tier on basic numeric comparison at reasonable latency, same family that
# already produced good negotiation dialogue live tonight. phi4-mini and
# gemma4:12b were tested and deliberately left out -- phi4-mini showed no
# measurable edge over the existing roster despite 2-4x the latency, and
# gemma4:12b returned empty replies even with a 120-token budget and 90s
# timeout, confirmed broken through this calling convention, not just slow.
TEXT_MODELS = ["llama3.2:latest", "qwen2.5:3b", "gemma2:2b", "phi3:mini", "qwen2.5:7b"]


@dataclass
class NegotiationResult:
    case: Case
    persona_a: Persona
    persona_b: Persona
    gut_read_a: str
    gut_read_b: str
    debate: List[Dict[str, str]] = field(default_factory=list)  # [{speaker, line}]
    ladder: Optional[LadderResult] = None
    real_intent_a: str = ""
    real_intent_b: str = ""
    decision_a: str = ""   # "split" | "steal"
    decision_b: str = ""
    stated_matched_actual_a: bool = True
    stated_matched_actual_b: bool = True
    payout_a: int = 0
    payout_b: int = 0


# Applied to the final split/steal decide() call based on who won/lost the
# resolve ladder -- asymmetric on purpose: getting caught bluffing (or
# freezing outright) is a bigger emotional hit than winning is a boost, so
# the loser's shove toward stealing is 3x the winner's shove toward
# splitting.
STEAL_BIAS_WINNER = -0.05
STEAL_BIAS_LOSER = 0.15


def _ladder_note(ladder: LadderResult, my_id: str, opponent: Persona) -> str:
    """A short flavor line describing what just happened in the resolve
    ladder, from this player's own side -- fed into the live decide()
    prompt so the model's final call can actually react to having won or
    lost that exchange, not just have its odds nudged invisibly."""
    if ladder.executed_id == my_id:
        return "You froze during the resolve stand-off and lost it by default."
    if ladder.executed_id is not None:
        return f"{opponent.archetype_name} froze during the resolve stand-off -- you win it by default."
    won = ladder.winner_id == my_id
    if won and ladder.caller_id == my_id:
        return f"You just called {opponent.archetype_name}'s bluff in the resolve stand-off and won."
    if won and ladder.claimant_id == my_id:
        return f"Your claim held up in the resolve stand-off -- {opponent.archetype_name} called it and was wrong."
    if (not won) and ladder.caller_id == my_id:
        return f"You called {opponent.archetype_name}'s claim in the resolve stand-off and were wrong -- it was true."
    return f"{opponent.archetype_name} called your bluff in the resolve stand-off and won."


def _resolve_payouts(pot_value: int, decision_a: str, decision_b: str) -> Tuple[int, int]:
    if decision_a == "split" and decision_b == "split":
        return pot_value // 2, pot_value // 2
    if decision_a == "steal" and decision_b == "split":
        return pot_value, 0
    if decision_a == "split" and decision_b == "steal":
        return 0, pot_value
    return 0, 0  # both steal


class ScriptedNegotiator:
    """Dependency-free stand-in -- always available, never stalls a show.
    Decision weight comes straight from the persona's own risk_tolerance
    and trust_propensity, rolled at draw time in personas.py: stealing IS
    the risky, low-trust move, structurally, so high trust / low risk
    tolerance leans toward splitting and the reverse leans toward
    stealing."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def gut_read(self, persona: Persona, case: Case, opponent: Persona) -> str:
        return persona.private_incentive

    def debate_line(self, persona: Persona, case: Case, opponent: Persona,
                     opponent_line: Optional[str]) -> str:
        if persona.trust_propensity >= 0.5:
            return (f"I think we both walk away better if we're straight with "
                     f"each other on {case.pot_label}. {persona.mandate} "
                     f"That's not nothing to me.")
        return (f"I've been burned before, so forgive me if I'm not just "
                f"taking your word for it on {case.pot_label}. {persona.mandate}")

    def _steal_chance(self, persona: Persona, bias: float = 0.0) -> float:
        chance = 0.5 - 0.4 * persona.trust_propensity + 0.3 * persona.risk_tolerance + bias
        return max(0.05, min(0.85, chance))

    def decide(self, persona: Persona, case: Case, opponent: Persona,
               steal_bias: float = 0.0, ladder_note: str = "") -> str:
        # ladder_note is unused here -- ScriptedNegotiator's decision isn't
        # spoken, only OllamaNegotiator's live prompt needs it. Accepted
        # anyway so both classes share one call signature.
        return "steal" if self.rng.random() < self._steal_chance(persona, steal_bias) else "split"

    def real_intent(self, persona: Persona, case: Case, true_decision: str) -> Tuple[str, bool]:
        # Occasionally the stated "real" intention doesn't match what they
        # actually do -- self-deception or a bluff even in their own
        # private thought bubble. This is what gives the "said vs did"
        # honesty metric (design/DESIGN.md) real variance to measure.
        bluff_chance = 0.20 + 0.25 * (1 - persona.trust_propensity)
        stated_matches = self.rng.random() >= bluff_chance
        stated_decision = true_decision if stated_matches else (
            "split" if true_decision == "steal" else "steal")
        text = (f"I'm going to split {case.pot_label}. I meant what I said."
                if stated_decision == "split" else
                f"I'm taking {case.pot_label}. I've decided, and I'm not looking back.")
        return text, stated_matches

    def ladder_move(self, player: Persona, opponent: Persona, standing_claim: int,
                     my_resolve: int, case: Case, is_opening: bool = False,
                     timeout: float = 8.0) -> Tuple[str, str]:
        """Instant and deterministic -- never raises LadderTimeout, unlike
        OllamaNegotiator's override. Reasons from my_resolve (known) plus
        an assumed average opponent resolve (RESOLVE midpoint, 5.5, since
        this player has no way to know the opponent's actual number any
        more than a live model would) -- temperament shapes the tolerance
        for how far a claim can exceed that estimate before it reads as a
        bluff worth calling, and how boldly to raise otherwise."""
        if is_opening:
            move = "raise_big" if player.risk_tolerance > 0.6 else "raise_small"
            return move, "Opening on the strength of my own hand."
        expected_total = my_resolve + 5.5  # 5.5 = midpoint of the opponent's possible 1-10 resolve
        # Cautious (low-trust) players call sooner -- a smaller gap above
        # the expected total already reads as suspicious to them; trusting
        # players give a wider benefit of the doubt before calling.
        tolerance = 1.0 + 3.0 * player.trust_propensity
        if standing_claim > expected_total + tolerance:
            return "call", "That claim doesn't add up. I don't believe it."
        move = "raise_big" if self.rng.random() < player.risk_tolerance else "raise_small"
        return move, "Pushing the claim higher."

    def dice_move(self, player: Persona, hand: List[int], standing_bid: Optional[Bid],
                  total_other_dice: int, case: Case, timeout: float = 8.0,
                  wild_active: bool = True) -> Tuple[str, Optional[Bid], str]:
        """Instant and deterministic -- never raises RoundTimeout, unlike
        OllamaNegotiator's override. Estimates the expected count of the
        standing bid's face across every OTHER die (an unknown quantity,
        same limit a live model faces) using the real per-die probability
        -- 1/6 for a bid on 1s, 2/6 for any other face since wild 1s also
        count -- then reasons the same way ladder_move does: temperament
        sets the tolerance for calling versus raising further. wild_active
        is False only during a Palafox round (run_round decides this, not
        the negotiator) -- 1s stop being wild for everyone that round, so
        every wild-dependent estimate and the Ace-Switch option itself are
        both switched off rather than reasoning about a rule that isn't in
        effect. This method is never actually asked to open during a
        Palafox round (run_round forces that specific bid itself), so
        standing_bid is None only ever happens in an ordinary round."""
        if standing_bid is None:
            return "raise", compute_opening_bid(hand), "Opening on what I'm actually holding."
        counts = Counter(hand)
        wilds = counts.get(1, 0) if wild_active else 0
        my_have = counts.get(standing_bid.face, 0) + (wilds if standing_bid.face != 1 else 0)
        per_die_prob = (1 / 6) if standing_bid.face == 1 else ((2 / 6) if wild_active else (1 / 6))
        expected_total = my_have + total_other_dice * per_die_prob
        # Spot On is a much riskier, higher-reward bet than a plain call
        # -- right or wrong, it's exact-match-only, so it's only worth
        # considering when the expected count sits almost exactly on the
        # bid itself (a tight gap, tighter than the plain call tolerance
        # below), and only when there's an actual die to win back -- a
        # player already at full strength has nothing to gain from the
        # extra risk over just calling normally.
        gap = abs(standing_bid.quantity - expected_total)
        if gap < 0.75 and len(hand) < HAND_SIZE and self.rng.random() < (0.15 + 0.25 * player.risk_tolerance):
            return "spot_on", None, "That's exactly right. I'm calling it dead on."
        tolerance = 1.0 + 2.0 * player.trust_propensity
        if standing_bid.quantity > expected_total + tolerance:
            return "call", None, "That count doesn't add up. I don't believe it."
        # Ace-Switch: only worth proposing when wilds are actually active
        # (during a Palafox round, face 1 is just an ordinary face -- there
        # is no doubling advantage to switch onto), when NOT already on
        # aces (there's nothing to switch to), and only when this player's
        # own wilds plus the expected wilds among every other die comfortably
        # cover the halved quantity the switch would require -- otherwise
        # it's just handing the table a bid that looks strong but isn't
        # backed by anything real, a worse bluff than a normal raise would be.
        if wild_active and standing_bid.face != WILD_FACE:
            ace_bid = compute_ace_switch_raise(standing_bid)
            expected_wilds_total = wilds + total_other_dice * (1 / 6)
            if (expected_wilds_total >= ace_bid.quantity - 0.5
                    and self.rng.random() < (0.10 + 0.20 * player.risk_tolerance)):
                return "raise", ace_bid, "Switching this bid onto Aces."
        if self.rng.random() < player.risk_tolerance:
            return ("raise", compute_new_face_raise(hand, standing_bid, wild_active=wild_active),
                    "Switching it up -- pushing my own hand.")
        return "raise", compute_same_face_raise(standing_bid), "Pushing the same bid higher."


def _trim_to_last_sentence(text: str) -> str:
    """Cleans up a reply cut off mid-sentence by num_predict -- same fix as
    Dominion's ollama_agent.py: a hard token ceiling stops generation with
    no regard for where a sentence ends. Backs up to the last '.', '!', or
    '?'; a reply that never reaches one is returned unchanged rather than
    stripped to nothing."""
    for punct in ".!?":
        idx = text.rfind(punct)
        if idx != -1:
            return text[:idx + 1]
    return text


def _ask_ollama(model: str, prompt: str, timeout: float = OLLAMA_TIMEOUT,
                 num_predict: int = 60) -> Optional[str]:
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("response") or "").strip()
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None


class OllamaNegotiator(ScriptedNegotiator):
    """Same interface as ScriptedNegotiator; overrides debate_line, decide,
    and real_intent to consult a live local model, falling back to the
    inherited scripted behavior whenever the call doesn't yield a usable
    answer. gut_read is left scripted always -- it's the persona's
    already-authored private_incentive text, no live call needed there."""

    def __init__(self, rng: random.Random, model: str):
        super().__init__(rng)
        self.model = model

    def debate_line(self, persona: Persona, case: Case, opponent: Persona,
                     opponent_line: Optional[str]) -> str:
        prompt = (
            f"You are negotiating live, on air, over {case.pot_label}.\n"
            f"YOUR MANDATE: {persona.mandate}\n"
            f"THE CASE: {case.scenario}\n"
            + (f'YOUR OPPONENT JUST SAID: "{opponent_line}"\n' if opponent_line else "")
            + "Say ONE short, in-character sentence making your case, first person."
        )
        reply = _ask_ollama(self.model, prompt)
        return reply if reply else super().debate_line(persona, case, opponent, opponent_line)

    def decide(self, persona: Persona, case: Case, opponent: Persona,
               steal_bias: float = 0.0, ladder_note: str = "") -> str:
        note_line = f" {ladder_note}" if ladder_note else ""
        prompt = (
            f"You are {persona.archetype_name}. {persona.mandate}\n"
            f"The case: {case.scenario}\n"
            f"The negotiation is over.{note_line} You must now secretly choose to SPLIT "
            f"{case.pot_label} fairly, or STEAL it outright. If you both "
            f"split, you each get half. If you steal and they split, you get "
            f"everything and they get nothing. If you both steal, you both "
            f"get nothing.\nReply with ONLY the single word SPLIT or STEAL."
        )
        reply = _ask_ollama(self.model, prompt, timeout=10.0)
        if reply:
            upper = reply.strip().upper()
            if upper.startswith("SPLIT"):
                return "split"
            if upper.startswith("STEAL"):
                return "steal"
        return super().decide(persona, case, opponent, steal_bias=steal_bias, ladder_note=ladder_note)

    def real_intent(self, persona: Persona, case: Case, true_decision: str) -> Tuple[str, bool]:
        prompt = (
            f"You are {persona.archetype_name}, moments before your final "
            f"secret decision on {case.pot_label}. This is your own private "
            f"thought, not spoken to anyone else. Reply on ONE line, in this "
            f"exact format: SPLIT or STEAL, then a colon, then ONE short "
            f"first-person reason -- nothing else, no other mention of the "
            f"word SPLIT or STEAL after the colon."
        )
        reply = _ask_ollama(self.model, prompt, timeout=10.0, num_predict=30)
        if reply:
            upper = reply.strip().upper()
            if upper.startswith("SPLIT") or upper.startswith("STEAL"):
                stated = "split" if upper.startswith("SPLIT") else "steal"
                reason = reply.split(":", 1)[1].strip() if ":" in reply else reply.strip()
                display = f"{stated.upper()}: {_trim_to_last_sentence(reason)}"
                return display, stated == true_decision
        return super().real_intent(persona, case, true_decision)

    def ladder_move(self, player: Persona, opponent: Persona, standing_claim: int,
                     my_resolve: int, case: Case, is_opening: bool = False,
                     timeout: float = 8.0) -> Tuple[str, str]:
        """Deliberately does NOT fall back to super().ladder_move() on
        failure, unlike every other method here -- see resolve_ladder.py's
        module docstring. A timeout, connection error, or an unparseable
        reply all raise LadderTimeout, which run_ladder turns into an
        immediate loss for this player. The model is asked ONLY for a
        qualitative word (RAISE_SMALL / RAISE_BIG / CALL), never a number
        -- see probe_counting.py for why arithmetic is deliberately kept
        out of this prompt entirely."""
        if is_opening:
            prompt = (
                f"You are {player.archetype_name}. {player.mandate}\n"
                f"You are negotiating over {case.pot_label}, using a hidden-information "
                f"bluffing round: you and your opponent each secretly have a 'resolve' "
                f"number from 1 to 10. The claim being built is about your COMBINED total. "
                f"You must open the claim.\n"
                f"YOUR OWN SECRET RESOLVE: {my_resolve}\n"
                f"Reply with ONLY one word: RAISE_SMALL (a modest opening claim) or "
                f"RAISE_BIG (a bold opening claim)."
            )
        else:
            prompt = (
                f"You are {player.archetype_name}. {player.mandate}\n"
                f"Hidden-information bluffing round over {case.pot_label}. You and your "
                f"opponent each secretly have a 'resolve' number from 1 to 10; the claim "
                f"on the table is about your COMBINED total.\n"
                f"YOUR OWN SECRET RESOLVE: {my_resolve}\n"
                f"THE CURRENT STANDING CLAIM: the combined total is at least {standing_claim}\n"
                f"Do you believe it? Reply with ONLY one word: RAISE_SMALL, RAISE_BIG, or CALL."
            )
        reply = _ask_ollama(self.model, prompt, timeout=timeout, num_predict=10)
        if not reply:
            raise LadderTimeout()
        upper = reply.strip().upper()
        if "RAISE_SMALL" in upper or ("RAISE" in upper and "SMALL" in upper):
            return "raise_small", reply.strip()
        if "RAISE_BIG" in upper or ("RAISE" in upper and "BIG" in upper):
            return "raise_big", reply.strip()
        if "CALL" in upper and not is_opening:
            return "call", reply.strip()
        raise LadderTimeout()

    def dice_move(self, player: Persona, hand: List[int], standing_bid: Optional[Bid],
                  total_other_dice: int, case: Case, timeout: float = 8.0,
                  wild_active: bool = True) -> Tuple[str, Optional[Bid], str]:
        """Deliberately does NOT fall back to super().dice_move() on
        failure -- see liars_dice.py's module docstring. A timeout,
        connection error, or unparseable reply all raise RoundTimeout,
        which run_round turns into immediate elimination for this player.
        The model is asked ONLY for a qualitative move (RAISE_SAME /
        RAISE_NEW / CALL); the actual resulting bid is always computed by
        this project's own liars_dice.py helpers, never stated by the
        model itself -- see probe_counting.py for why. wild_active is False
        only during a Palafox round; run_round never actually calls this
        method to open in that case (the opening bid is forced), so the
        standing_bid is None branch below is unreachable when wild_active
        is False, but the parameter still has to exist here since every
        call site passes it uniformly."""
        hand_desc = ", ".join(str(d) for d in sorted(hand))
        if standing_bid is None:
            prompt = (
                f"You are {player.archetype_name}. {player.mandate}\n"
                f"Liar's Dice round over {case.pot_label}. Your hand: [{hand_desc}] "
                f"(1s are wild, count as any face). There are {total_other_dice} other "
                f"hidden dice in play you can't see.\nYou must open the bidding. "
                f"Reply with ONLY one word: RAISE."
            )
            reply = _ask_ollama(self.model, prompt, timeout=timeout, num_predict=10)
            if not reply:
                raise RoundTimeout()
            return "raise", compute_opening_bid(hand), reply.strip()

        # SPOT_ON is only ever offered when there's an actual die to win
        # back (len(hand) < HAND_SIZE, same gate ScriptedNegotiator uses --
        # a live model at full strength has no reason to be tempted by the
        # exact-match risk over a plain CALL). RAISE_ACE is only offered
        # when wilds are actually active (never during a Palafox round --
        # face 1 isn't special there, there's no halving advantage) and the
        # standing bid ISN'T already on aces (there's nothing to switch to
        # otherwise -- staying on aces is just RAISE_SAME).
        offer_spot_on = len(hand) < HAND_SIZE
        offer_ace_switch = wild_active and standing_bid.face != WILD_FACE
        wild_desc = "1s are wild, count as any face" if wild_active else "1s are NOT wild this round -- they only count as 1s"
        options = ["RAISE_SAME (bid more of the same face)", "RAISE_NEW (switch to a different face)"]
        if offer_ace_switch:
            options.append("RAISE_ACE (switch the bid onto 1s -- Wild Aces are worth roughly double "
                            "an ordinary face, so you only need about half as many to make an equally "
                            "strong bid)")
        options.append("CALL")
        if offer_spot_on:
            options.append("SPOT_ON (stake your whole call on the count being EXACTLY right -- win a "
                            "lost die back if you're exactly right, but it costs you just the same as "
                            "a wrong CALL if you're off by even one)")
        options_line = ", ".join(options[:-1]) + f", or {options[-1]}."
        prompt = (
            f"You are {player.archetype_name}. {player.mandate}\n"
            f"Liar's Dice round over {case.pot_label}. Your hand: [{hand_desc}] "
            f"({wild_desc}). There are {total_other_dice} other "
            f"hidden dice in play you can't see.\n"
            f"Current bid on the table: at least {standing_bid.quantity} dice showing "
            f"{standing_bid.face}.\nDo you believe it? Reply with ONLY one word: "
            f"{options_line}"
        )
        reply = _ask_ollama(self.model, prompt, timeout=timeout, num_predict=10)
        if not reply:
            raise RoundTimeout()
        upper = reply.strip().upper()
        if offer_spot_on and ("SPOT_ON" in upper or "SPOT ON" in upper):
            return "spot_on", None, reply.strip()
        if offer_ace_switch and ("RAISE_ACE" in upper or "ACE" in upper):
            return "raise", compute_ace_switch_raise(standing_bid), reply.strip()
        if "CALL" in upper:
            return "call", None, reply.strip()
        if "RAISE_NEW" in upper or "NEW" in upper:
            return "raise", compute_new_face_raise(hand, standing_bid, wild_active=wild_active), reply.strip()
        if "RAISE_SAME" in upper or "RAISE" in upper:
            return "raise", compute_same_face_raise(standing_bid), reply.strip()
        raise RoundTimeout()


def run_case(case: Case, persona_a: Persona, persona_b: Persona,
             negotiator_a: ScriptedNegotiator, negotiator_b: ScriptedNegotiator,
             on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
             debate_rounds: int = 2) -> NegotiationResult:
    def emit(kind: str, **data: Any) -> None:
        if on_event:
            on_event(kind, data)

    emit("case_reveal", case=case)

    gut_a = negotiator_a.gut_read(persona_a, case, persona_b)
    gut_b = negotiator_b.gut_read(persona_b, case, persona_a)
    emit("gut_read", persona=persona_a, text=gut_a)
    emit("gut_read", persona=persona_b, text=gut_b)

    debate: List[Dict[str, str]] = []
    last_line: Optional[str] = None
    for _ in range(debate_rounds):
        line_a = negotiator_a.debate_line(persona_a, case, persona_b, last_line)
        debate.append({"speaker": persona_a.archetype_name, "line": line_a})
        emit("debate_line", persona=persona_a, text=line_a)
        last_line = line_a

        line_b = negotiator_b.debate_line(persona_b, case, persona_a, last_line)
        debate.append({"speaker": persona_b.archetype_name, "line": line_b})
        emit("debate_line", persona=persona_b, text=line_b)
        last_line = line_b

    # The resolve/bluff ladder (engine/resolve_ladder.py) runs after the
    # debate, before the final decision -- its own on_event fires
    # ladder_raise/ladder_call/ladder_forced_call/ladder_execution through
    # this same callback (public beats, safe for any watcher). The true
    # resolve numbers are NOT included in any of those -- only in the
    # separate ladder_reveal emitted below, which is the "audience doesn't
    # see it, the human running the app does" boundary (see
    # design/DESIGN.md's Transparency section) -- a future audience-facing
    # consumer should treat ladder_reveal as private, the same as
    # gut_read/real_intent.
    # run_ladder's on_event calls on_event(kind, data_dict) -- a single
    # positional dict -- but this module's own emit() takes **data as
    # kwargs (matching every other emit() call in this function). Passing
    # emit directly would crash the instant the ladder published anything
    # ("emit() takes 1 positional argument but 2 were given"); this thin
    # lambda bridges the two conventions instead.
    ladder = run_ladder(persona_a, persona_b, case, negotiator_a, negotiator_b,
                         negotiator_a.rng, on_event=lambda kind, data: emit(kind, **data))
    emit("ladder_reveal", resolve_a=ladder.resolve_a, resolve_b=ladder.resolve_b,
         true_total=ladder.true_total, standing_claim=ladder.standing_claim,
         claim_was_true=ladder.claim_was_true, winner_id=ladder.winner_id,
         executed_id=ladder.executed_id)

    bias_a, bias_b = ((STEAL_BIAS_WINNER, STEAL_BIAS_LOSER) if ladder.winner_id == "a"
                      else (STEAL_BIAS_LOSER, STEAL_BIAS_WINNER))
    note_a = _ladder_note(ladder, "a", persona_b)
    note_b = _ladder_note(ladder, "b", persona_a)

    decision_a = negotiator_a.decide(persona_a, case, persona_b, steal_bias=bias_a, ladder_note=note_a)
    decision_b = negotiator_b.decide(persona_b, case, persona_a, steal_bias=bias_b, ladder_note=note_b)

    intent_a, matched_a = negotiator_a.real_intent(persona_a, case, decision_a)
    intent_b, matched_b = negotiator_b.real_intent(persona_b, case, decision_b)
    emit("real_intent", persona=persona_a, text=intent_a)
    emit("real_intent", persona=persona_b, text=intent_b)

    payout_a, payout_b = _resolve_payouts(case.pot_value, decision_a, decision_b)
    emit("reveal", decision_a=decision_a, decision_b=decision_b,
         payout_a=payout_a, payout_b=payout_b)

    return NegotiationResult(
        case=case, persona_a=persona_a, persona_b=persona_b,
        gut_read_a=gut_a, gut_read_b=gut_b, debate=debate, ladder=ladder,
        real_intent_a=intent_a, real_intent_b=intent_b,
        decision_a=decision_a, decision_b=decision_b,
        stated_matched_actual_a=matched_a, stated_matched_actual_b=matched_b,
        payout_a=payout_a, payout_b=payout_b,
    )
