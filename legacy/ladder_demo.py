"""LEGACY -- proves the 2-party resolve/bluff ladder from the superseded
pot/split-steal game (the project is Life Rolls / Liar's Dice now, see
design/DESIGN.md's Liar's Dice tournament section for the current
mechanic's own proof script, dice_demo.py).

    python legacy/ladder_demo.py                 scripted vs scripted
    python legacy/ladder_demo.py --live           live Ollama vs live Ollama
    python legacy/ladder_demo.py --force-timeout  proves the execution path fires
"""
from __future__ import annotations
import argparse
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.cases import CASE_LIBRARY
from engine.negotiation import OllamaNegotiator, ScriptedNegotiator, TEXT_MODELS
from engine.personas import draw_cast
from engine.resolve_ladder import run_ladder


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError):
        return False


def on_event(kind, data):
    if kind == "ladder_raise":
        print(f"  {data['player_id'].upper()} {data['move']:11s} -> claim now {data['claim']:2d}   ({data['reason']})")
    elif kind == "ladder_call":
        print(f"  {data['caller_id'].upper()} CALLS {data['claimant_id'].upper()}'s claim of {data['standing_claim']}   ({data['reason']})")
    elif kind == "ladder_forced_call":
        print(f"  [safety valve] forcing a call after too many raises")
    elif kind == "ladder_execution":
        print(f"  *** {data['player_id'].upper()} FROZE -- EXECUTED for hesitating ***")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--force-timeout", action="store_true",
                         help="Sets an impossibly tiny live timeout to prove the execution path fires")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    case = rng.choice(CASE_LIBRARY)
    persona_a, persona_b = draw_cast(2, rng)

    use_live = (args.live or args.force_timeout) and _ollama_reachable()
    if use_live:
        model_a, model_b = rng.sample(TEXT_MODELS, k=2)
        negotiator_a, negotiator_b = OllamaNegotiator(rng, model_a), OllamaNegotiator(rng, model_b)
        print(f"live: {persona_a.archetype_name} -> {model_a}, {persona_b.archetype_name} -> {model_b}")
    else:
        negotiator_a, negotiator_b = ScriptedNegotiator(rng), ScriptedNegotiator(rng)

    print(f"Case: {case.name}  [{case.framework}]\n")

    if args.force_timeout:
        if not use_live:
            print("--force-timeout needs Ollama reachable to actually prove the path; aborting.")
            return
        # Monkeypatch a laughably small timeout onto both negotiators'
        # calls -- 0.001s guarantees urlopen times out before any real
        # model could possibly reply, which is the whole point: prove
        # run_ladder's except LadderTimeout branch actually fires and
        # ends the game, rather than just trusting it would.
        import engine.negotiation as neg
        real_ask = neg._ask_ollama
        neg._ask_ollama = lambda model, prompt, timeout=8.0, num_predict=60: real_ask(
            model, prompt, timeout=0.001, num_predict=num_predict)

    result = run_ladder(persona_a, persona_b, case, negotiator_a, negotiator_b, rng, on_event=on_event)

    print(f"\nTrue resolve: {persona_a.archetype_name}={result.resolve_a}  "
          f"{persona_b.archetype_name}={result.resolve_b}  (total={result.true_total})")
    if result.executed_id:
        winner_name = persona_a.archetype_name if result.winner_id == "a" else persona_b.archetype_name
        loser_name = persona_a.archetype_name if result.executed_id == "a" else persona_b.archetype_name
        print(f"RESULT: {loser_name} was executed for freezing. {winner_name} wins by default.")
    else:
        claimant_name = persona_a.archetype_name if result.claimant_id == "a" else persona_b.archetype_name
        caller_name = persona_a.archetype_name if result.caller_id == "a" else persona_b.archetype_name
        winner_name = persona_a.archetype_name if result.winner_id == "a" else persona_b.archetype_name
        verdict = "TRUE" if result.claim_was_true else "A BLUFF"
        print(f"RESULT: {claimant_name}'s claim of {result.standing_claim} was {verdict} "
              f"(actual total {result.true_total}). {caller_name} called it. {winner_name} wins.")


if __name__ == "__main__":
    main()
