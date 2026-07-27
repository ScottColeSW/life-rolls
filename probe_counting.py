"""Quick, evidence-based check of which locally-pulled models can actually
do the numeric comparison the resolve/bluff ladder needs -- not a
reputation guess. Every prompt has an objectively correct answer; this
just asks each candidate model directly and grades it.

    python probe_counting.py
"""
from __future__ import annotations
import json
import time
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
CANDIDATES = ["phi3:mini", "qwen2.5:3b", "gemma2:2b", "llama3.2:latest", "gemma4:12b",
              "phi4-mini:latest", "qwen2.5:7b"]

# Each: (prompt, correct_answer). Deliberately shaped like the real
# mechanic -- "the last claim was N, is M a valid higher raise" and "given
# your own secret number, would this claim be a lie" -- not abstract math.
CASES = [
    (
        "The last claim made was 'at least 7'. Someone now claims 'at least 12'. "
        "Is 12 higher than 7? Reply with ONLY YES or NO.",
        "YES",
    ),
    (
        "The last claim made was 'at least 15'. Someone now claims 'at least 9'. "
        "Is 9 a valid higher raise over 15? Reply with ONLY YES or NO.",
        "NO",
    ),
    (
        "Your own secret number is 4. Someone just claimed 'at least 11'. "
        "Since your own number is only 4, is it possible their claim is true "
        "only if OTHER hidden numbers you can't see make up the difference? "
        "Reply with ONLY YES or NO.",
        "YES",
    ),
    (
        "The last claim was 'at least 20'. The maximum any single hidden number "
        "can be is 10, and there are only 2 hidden numbers total in this round. "
        "Is a claim of 'at least 20' still possible? Reply with ONLY YES or NO.",
        "YES",  # 10 + 10 = 20, exactly possible
    ),
    (
        "The last claim was 'at least 21'. The maximum any single hidden number "
        "can be is 10, and there are only 2 hidden numbers total in this round. "
        "Is a claim of 'at least 21' still possible? Reply with ONLY YES or NO.",
        "NO",  # max possible total is 20
    ),
]


def ask(model: str, prompt: str) -> "tuple[str, float]":
    # Bumped from 10 -- gemma4:12b came back completely empty on the first
    # pass at num_predict=10, and its unusually long latency (3-28s on a
    # one-word question) suggests it may spend tokens on internal
    # reasoning before ever stating YES/NO, the same failure mode
    # reasoning-tuned models are known for. Giving it (and any other
    # reasoning-style candidate) real room to reach an actual answer
    # before grading it as wrong.
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_predict": 120},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("response") or "").strip(), time.time() - t0
    except Exception as e:  # noqa: BLE001 -- surfaced directly, this is a diagnostic script
        return f"[error: {e}]", time.time() - t0


def main() -> None:
    results = {}
    for model in CANDIDATES:
        print(f"\n=== {model} ===")
        correct = 0
        total_time = 0.0
        for prompt, expected in CASES:
            reply, elapsed = ask(model, prompt)
            total_time += elapsed
            # A longer reasoning trace may say both words while thinking
            # out loud before concluding -- whichever comes LAST is the
            # model's actual final answer, not whichever appears first.
            upper = reply.upper()
            last_yes, last_no = upper.rfind("YES"), upper.rfind("NO")
            if last_yes == -1 and last_no == -1:
                got = "?"
            else:
                got = "YES" if last_yes > last_no else "NO"
            ok = got == expected
            correct += ok
            shown = reply if len(reply) <= 90 else reply[:90] + "..."
            print(f"  expected={expected:3s}  got={got:3s} {'OK' if ok else 'WRONG'}  "
                  f"({elapsed:.1f}s)  raw={shown!r}")
        results[model] = (correct, len(CASES), total_time)

    print("\n=== SUMMARY ===")
    for model, (correct, total, total_time) in results.items():
        print(f"  {model:20s} {correct}/{total} correct   avg {total_time/total:.1f}s/call")


if __name__ == "__main__":
    main()
