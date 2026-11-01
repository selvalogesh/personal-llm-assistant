"""Measures the Router's classifier on the labelled set in tests/ (CR-4 D12).

It calls OpenRouter with the Router's own prompt, schema and checks, so it runs on purpose before a release, never in
pytest. A run costs well under a cent.

    python tools/eval_classifier.py [--model MODEL] [--min 42]

The key comes from OPENROUTER_API_KEY, or from the file given with --key-file. Exits 1 below --min right answers.
"""

import argparse
import asyncio
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_router():
    spec = importlib.util.spec_from_file_location("router", ROOT / "functions" / "router.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


router = load_router()


def expected(item: dict) -> list[dict]:
    """The labels that count as right: `want`, then any alternatives in `ok`, read as the Router reads them."""
    voice_on = bool(item.get("voice_mode"))
    return [router.labels_of(router.read_intent(w, voice_on, True)) for w in [item["want"], *item.get("ok", [])]]


def judge(item: dict, intent: dict) -> bool:
    if router.labels_of(intent) not in expected(item):
        return False
    if ("corrects" in item) != (intent["corrects"] is not None):
        return False
    return all(word in intent["image_prompt"].lower() for word in item.get("prompt_has", []))


async def classify(pipe, key, item):
    started = time.monotonic()
    try:
        intent, cost = await pipe._classify(
            key,
            "",
            item["msg"],
            prev_text=item.get("prev", ""),
            last_image=item.get("last") == "image",
            voice_on=bool(item.get("voice_mode")),
        )
        return item, intent, cost, time.monotonic() - started, None
    except Exception as e:
        return item, None, 0.0, time.monotonic() - started, repr(e)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="classifier model (default: the Router's CLASSIFIER_MODEL)")
    parser.add_argument("--min", type=int, default=42, help="fewest right answers that pass (default: 42)")
    parser.add_argument("--key-file", help="file holding the OpenRouter key (default: OPENROUTER_API_KEY)")
    args = parser.parse_args()

    key = Path(args.key_file).read_text().strip() if args.key_file else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("No key: set OPENROUTER_API_KEY or pass --key-file.", file=sys.stderr)
        return 2
    pipe = router.Pipe()
    if args.model:
        pipe.valves.CLASSIFIER_MODEL = args.model
    lines = (ROOT / "tests" / "classifier_intents.jsonl").read_text(encoding="utf-8").splitlines()
    items = [json.loads(line) for line in lines if line.strip()]

    gate = asyncio.Semaphore(6)

    async def limited(item):
        async with gate:
            return await classify(pipe, key, item)

    results = await asyncio.gather(*(limited(item) for item in items))
    right, times, cost = 0, sorted(r[3] for r in results), sum(r[2] for r in results)
    for item, intent, _, _, error in results:
        if error:
            print(f"ERROR {item['msg'][:70]!r}: {error}")
        elif judge(item, intent):
            right += 1
        else:
            print(f"MISS  {item['msg'][:70]!r}: got {router.describe(router.labels_of(intent))}"
                  f"{', corrects previous' if intent['corrects'] else ''}, want {router.describe(expected(item)[0])}")
    print(
        f"\n{pipe.valves.CLASSIFIER_MODEL}: {right}/{len(items)} right, median {statistics.median(times):.2f} s, "
        f"slowest {times[-1]:.2f} s, ${cost / len(items):.6f} a call"
    )
    return 0 if right >= args.min else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
