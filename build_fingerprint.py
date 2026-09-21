#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from audio_fingerprint import HOP_SIZE, SAMPLE_RATE, build_index, decode_audio


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a compact spectral fingerprint for one target sound."
    )
    parser.add_argument(
        "audio",
        help="Reference audio (ogg/mp3/wav/...; decoded with ffmpeg)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="target_fingerprint.json",
    )
    parser.add_argument(
        "--start",
        type=float,
        default=0.06,
        help="Seconds to skip at the start",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=1.20,
        help="How much of the onset to fingerprint",
    )
    args = parser.parse_args()

    audio = decode_audio(args.audio)
    start = max(0, int(args.start * SAMPLE_RATE))
    end = min(
        len(audio),
        start + max(1, int(args.seconds * SAMPLE_RATE)),
    )
    segment = audio[start:end]
    index = build_index(segment)

    payload = {
        "version": 1,
        "sample_rate": SAMPLE_RATE,
        "hop_size": HOP_SIZE,
        "reference_start_seconds": args.start,
        "reference_seconds": len(segment) / SAMPLE_RATE,
        "hash_count": sum(len(v) for v in index.values()),
        "index": {
            f"{a},{b},{dt}": times
            for (a, b, dt), times in index.items()
        },
    }

    Path(args.output).write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
    )

    print(
        f"wrote {args.output}: "
        f"{len(index)} unique hashes, "
        f"{payload['hash_count']} total"
    )


if __name__ == "__main__":
    main()
