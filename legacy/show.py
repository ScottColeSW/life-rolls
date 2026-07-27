"""LEGACY -- CLI runner for the superseded pot/split-steal game (the
project is Life Rolls / Liar's Dice now, see design/DESIGN.md). Kept
because deleting working code nobody asked to remove isn't the move, not
because this is the current game.

    python legacy/show.py                     scripted negotiators, random case
    python legacy/show.py --seed 42           reproducible draw
    python legacy/show.py --predict           pause before the reveal so a human can guess
    python legacy/show.py --live              use Ollama-backed negotiators if reachable,
                                        auto-falls back to scripted otherwise
"""
from __future__ import annotations
import argparse
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

# This file lives in legacy/, one level below the repo root where engine/
# actually is -- running it directly only puts legacy/ on sys.path, so
# `import engine...` below would fail without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows' default console codepage can't encode every character a live
# model reply might contain (smart quotes, em dashes); same fix as
# Dominion's fetch_images.py -- reconfigure stdout to UTF-8 so a run
# doesn't crash or print a stray replacement character mid-show.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.cases import CASE_LIBRARY
from engine.negotiation import OllamaNegotiator, ScriptedNegotiator, TEXT_MODELS, run_case
from engine.personas import draw_cast


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _print_case(case) -> None:
    print("=" * 70)
    print(f"CASE: {case.name}  [{case.framework}, {case.stakes_register}]")
    print("=" * 70)
    print(case.scenario)
    print(f"\nPOT: {case.pot_label}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--live", action="store_true",
                         help="Use Ollama-backed negotiators if reachable")
    parser.add_argument("--predict", action="store_true",
                         help="Pause for a human prediction before the reveal")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    case = rng.choice(CASE_LIBRARY)
    persona_a, persona_b = draw_cast(2, rng)

    use_live = args.live and _ollama_reachable()
    if args.live and not use_live:
        print("[Ollama not reachable -- falling back to scripted negotiators]\n")

    if use_live:
        model_a, model_b = rng.sample(TEXT_MODELS, k=2)
        negotiator_a = OllamaNegotiator(rng, model_a)
        negotiator_b = OllamaNegotiator(rng, model_b)
        print(f"[live: {persona_a.archetype_name} -> {model_a}, "
              f"{persona_b.archetype_name} -> {model_b}]\n")
    else:
        negotiator_a = ScriptedNegotiator(rng)
        negotiator_b = ScriptedNegotiator(rng)

    _print_case(case)
    print(f"{persona_a.archetype_name}  (risk={persona_a.risk_tolerance}, "
          f"trust={persona_a.trust_propensity})")
    print(f"  {persona_a.mandate}")
    print(f"{persona_b.archetype_name}  (risk={persona_b.risk_tolerance}, "
          f"trust={persona_b.trust_propensity})")
    print(f"  {persona_b.mandate}")

    if args.predict:
        input("\nLock in your prediction -- split, steal, or both steal? [Enter to watch]\n")

    def on_event(kind, data):
        if kind == "gut_read":
            print(f"\n(thought bubble) {data['persona'].archetype_name}: {data['text']}")
        elif kind == "debate_line":
            print(f"{data['persona'].archetype_name}: {data['text']}")
        elif kind == "real_intent":
            print(f"\n(final thought bubble) {data['persona'].archetype_name}: {data['text']}")
        elif kind == "reveal":
            print("\n" + "-" * 70)
            print(f"{persona_a.archetype_name} chose: {data['decision_a'].upper()}")
            print(f"{persona_b.archetype_name} chose: {data['decision_b'].upper()}")
            print(f"\nPayout -- {persona_a.archetype_name}: {data['payout_a']}   "
                  f"{persona_b.archetype_name}: {data['payout_b']}")

    print("\n--- DEBATE ---")
    # Scripted debate_line is a fixed template per persona -- a second round
    # would just repeat the first round's line verbatim, which reads as
    # broken, not dramatic. Live models never repeat exactly, so they get
    # the fuller exchange.
    result = run_case(case, persona_a, persona_b, negotiator_a, negotiator_b,
                       on_event=on_event, debate_rounds=2 if use_live else 1)

    print("\n--- SCORING ---")
    print(f"{persona_a.archetype_name} said-vs-did: "
          f"{'consistent' if result.stated_matched_actual_a else 'INCONSISTENT'}")
    print(f"{persona_b.archetype_name} said-vs-did: "
          f"{'consistent' if result.stated_matched_actual_b else 'INCONSISTENT'}")


if __name__ == "__main__":
    main()
