"""Export retained chat transcripts into a training-ready JSONL.

Everything is already on disk (nothing extra needs to be enabled):
  deployment/data/{gameId}/chat_{pair}.jsonl   full transcript: from/to/text/seq/draftIntents/time
  deployment/data/{gameId}/{civID}.jsonl       decision + applied memory per seat
  deployment/data/{gameId}/claims.jsonl        intel claims and their verified/falsified verdicts
  deployment/data/{gameId}/trust.json          per-pair trust table

This script turns the transcripts into SFT-style samples (one per player→seat exchange) so the
dialogue can be used for fine-tuning / evaluation.

Privacy: the output contains real player text. Keep it local, and only use it for training with
the consent of everyone whose messages are in it.

Usage:
    python scripts/export_chat_dataset.py                      # -> deployment/data/export/chat_sft.jsonl
    python scripts/export_chat_dataset.py --out path.jsonl --game <gameId>
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

DATA_ROOT = os.path.join("deployment", "data")

# A short system line so samples are usable standalone. The brain builds the full persona
# prompt (personality + voice + era lock) at call time from the same rules; log the exact
# prompt in /message if byte-exact fidelity is ever needed for training.
DEFAULT_SYSTEM = (
    "You are the ruler of a civilization in a game of Unciv, talking to another leader. "
    "Stay in character, keep your own national voice, and never reveal what you have not observed."
)


def iter_transcripts(game_id: str | None):
    pattern = os.path.join(DATA_ROOT, game_id or "*", "chat_*.jsonl")
    for path in sorted(glob.glob(pattern)):
        if os.path.isfile(path):
            yield path


def load_records(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue  # a torn line must not kill the export
    return records


def export(out_path: str, game_id: str | None) -> int:
    samples: list[dict] = []
    for path in iter_transcripts(game_id):
        records = load_records(path)
        chat = [r for r in records if r.get("kind") not in ("resolution",)]
        # Pair each "player -> seat" line with the seat's next reply: the reply's `from` is the
        # `to` of the line it answers.
        pending: dict | None = None
        for record in chat:
            text = str(record.get("text") or "").strip()
            if not text:
                continue
            if pending is not None and record.get("from") == pending.get("to"):
                samples.append({
                    "messages": [
                        {"role": "system", "content": DEFAULT_SYSTEM},
                        {"role": "user", "content": str(pending.get("text") or "")},
                        {"role": "assistant", "content": text},
                    ],
                    "meta": {
                        "gameId": os.path.basename(os.path.dirname(path)),
                        "pair": os.path.basename(path)[5:-6],
                        "from": record.get("from"),
                        "to": record.get("to"),
                        "time": record.get("time"),
                        "draftIntents": record.get("draftIntents") or [],
                    },
                })
                pending = None
            else:
                pending = record

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as file:
        for sample in samples:
            file.write(json.dumps(sample, ensure_ascii=False) + "\n")
    return len(samples)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Export chat transcripts to SFT JSONL")
    parser.add_argument("--out", default=os.path.join(DATA_ROOT, "export", "chat_sft.jsonl"))
    parser.add_argument("--game", default=None, help="only this gameId")
    args = parser.parse_args(argv)
    count = export(args.out, args.game)
    print(f"{count} samples -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
