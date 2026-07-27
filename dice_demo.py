"""Proves the Liar's Dice tournament actually works -- scripted, live, and
the timeout/execution path specifically (not just the happy path).

    python dice_demo.py                 5 scripted players
    python dice_demo.py --players 6     more seats
    python dice_demo.py --live          live Ollama players
    python dice_demo.py --force-timeout proves the execution path fires
"""
from __future__ import annotations
import argparse
import random
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.cases import CASE_LIBRARY
from engine.liars_dice import run_tournament
from engine.negotiation import OllamaNegotiator, ScriptedNegotiator, TEXT_MODELS
from engine.personas import draw_cast


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2.0)
        return True
    except (urllib.error.URLError, OSError):
        return False


def on_event(names):
    def handler(kind, data):
        if kind == "round_start":
            print(f"\n--- new round, dice re-rolled ({len(data['alive_indices'])} players still in) ---")
        elif kind == "round_raise":
            b = data["bid"]
            print(f"  {names[data['player_index']]} raises: at least {b.quantity} showing {b.face}  ({data['reason']})")
        elif kind == "round_call":
            verdict = "TRUE" if data["bid_was_true"] else "A BLUFF"
            print(f"  {names[data['caller_index']]} CALLS {names[data['claimant_index']]}'s bid "
                  f"-- actual count {data['actual_count']}, bid was {verdict}")
        elif kind == "round_execution":
            print(f"  *** {names[data['player_index']]} FROZE -- EXECUTED for hesitating ***")
        elif kind == "player_eliminated":
            print(f"  >>> {names[data['player_index']]} is OUT <<<")
        elif kind == "tournament_winner":
            print(f"\n=== {names[data['player_index']]} WINS THE POT ===")
    return handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--players", type=int, default=5)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--force-timeout", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    case = rng.choice(CASE_LIBRARY)
    personas = draw_cast(args.players, rng)
    names = [p.archetype_name for p in personas]

    use_live = (args.live or args.force_timeout) and _ollama_reachable()
    if use_live:
        models = [rng.choice(TEXT_MODELS) for _ in personas]
        negotiators = [OllamaNegotiator(rng, m) for m in models]
        for n, m in zip(names, models):
            print(f"{n} -> {m}")
    else:
        negotiators = [ScriptedNegotiator(rng) for _ in personas]

    print(f"\nCase: {case.name}  [{case.framework}]")
    print("Seated: " + ", ".join(names))

    if args.force_timeout:
        if not use_live:
            print("--force-timeout needs Ollama reachable; aborting.")
            return
        import engine.negotiation as neg
        real_ask = neg._ask_ollama
        neg._ask_ollama = lambda model, prompt, timeout=8.0, num_predict=60: real_ask(
            model, prompt, timeout=0.001, num_predict=num_predict)

    result = run_tournament(personas, negotiators, case, rng, on_event=on_event(names))

    print(f"\nElimination order: {' -> '.join(names[i] for i in result.order_of_elimination)}")
    print(f"Winner: {names[result.winner_index]}")
    print(f"Total rounds played: {len(result.rounds)}")


if __name__ == "__main__":
    main()
