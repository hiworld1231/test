#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

from audio_fingerprint import SAMPLE_RATE, decode_audio, match


def load_index(path: str | Path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    if data.get("version") != 1 or data.get("sample_rate") != SAMPLE_RATE:
        raise RuntimeError("unsupported fingerprint format")

    index = {}

    for key, times in data["index"].items():
        a, b, dt = (int(x) for x in key.split(","))
        index[(a, b, dt)] = [int(t) for t in times]

    return index, data


def run_text(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or f"{' '.join(cmd)} failed"
        )

    return proc.stdout.strip()


def pulse_monitor_source(requested: str) -> str:
    if requested != "auto":
        return requested

    if not shutil.which("pactl"):
        raise RuntimeError(
            "pactl not found (on Arch/CachyOS: sudo pacman -S libpulse)"
        )

    sink = run_text(["pactl", "get-default-sink"]).splitlines()[0].strip()
    wanted = sink + ".monitor"

    sources = []

    for line in run_text(["pactl", "list", "short", "sources"]).splitlines():
        parts = line.split("\t")

        if len(parts) >= 2:
            sources.append(parts[1])

    if wanted in sources:
        return wanted

    monitors = [s for s in sources if s.endswith(".monitor")]

    if monitors:
        return monitors[0]

    raise RuntimeError(
        "no Pulse/PipeWire monitor source found; "
        "try: pactl list short sources"
    )


def execute_action(command: str, event_no: int, result) -> None:
    now = time.strftime("%H:%M:%S")

    print(
        f"\n[{now}] TARGET DETECTED #{event_no} "
        f"votes={result.votes} "
        f"runner_up={result.runner_up} "
        f"matched={result.matched_hashes}",
        flush=True,
    )

    if command:
        env = os.environ.copy()
        env.update(
            {
                "SOUND_MATCH_VOTES": str(result.votes),
                "SOUND_MATCH_RUNNER_UP": str(result.runner_up),
                "SOUND_MATCHED_HASHES": str(result.matched_hashes),
            }
        )

        subprocess.Popen(
            command,
            shell=True,
            env=env,
        )


def is_detection(result, threshold: int, ratio: float) -> bool:
    if result.votes < threshold:
        return False

    return (
        result.runner_up == 0
        or result.votes >= result.runner_up * ratio
    )


def detect_array(index, audio: np.ndarray, args) -> int:
    window_samples = int(args.window * SAMPLE_RATE)
    step_samples = max(1, int(args.step * SAMPLE_RATE))
    first_end = max(int(0.30 * SAMPLE_RATE), step_samples)

    events = 0
    last = -1e9

    for end in range(
        first_end,
        len(audio) + step_samples,
        step_samples,
    ):
        chunk = audio[
            max(0, end - window_samples):
            min(end, len(audio))
        ]

        if len(chunk) < int(0.30 * SAMPLE_RATE):
            continue

        result = match(index, chunk)

        if args.debug:
            print(
                f"t={min(end, len(audio))/SAMPLE_RATE:6.2f}s "
                f"votes={result.votes:4d} "
                f"runner={result.runner_up:3d}",
                flush=True,
            )

        now = end / SAMPLE_RATE

        if (
            is_detection(result, args.threshold, args.ratio)
            and now - last >= args.cooldown
        ):
            events += 1
            execute_action(
                args.command,
                events,
                result,
            )
            last = now

            if args.once:
                return events

    return events


def detect_live(index, args) -> int:
    if not shutil.which("parec"):
        raise RuntimeError(
            "parec not found (on Arch/CachyOS: sudo pacman -S libpulse)"
        )

    source = pulse_monitor_source(args.source)

    print(f"source: {source}")
    print(
        f"listening: {SAMPLE_RATE} Hz mono | "
        f"window={args.window:.2f}s | "
        f"threshold={args.threshold}"
    )
    print("Ctrl+C to stop.\n")

    cmd = [
        "parec",
        "--raw",
        f"--device={source}",
        "--format=float32le",
        f"--rate={SAMPLE_RATE}",
        "--channels=1",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert proc.stdout is not None

    step_samples = max(
        1,
        int(args.step * SAMPLE_RATE),
    )
    window_samples = max(
        step_samples,
        int(args.window * SAMPLE_RATE),
    )

    blocks = deque()
    buffered = 0
    events = 0
    last_detection = -1e9
    last_debug = 0.0

    try:
        while True:
            raw = proc.stdout.read(step_samples * 4)

            if not raw:
                stderr = (
                    proc.stderr.read().decode(
                        "utf-8",
                        errors="replace",
                    )
                    if proc.stderr
                    else ""
                )

                raise RuntimeError(
                    stderr.strip() or "parec stopped"
                )

            block = np.frombuffer(
                raw,
                dtype="<f4",
            ).astype(
                np.float32,
                copy=True,
            )

            blocks.append(block)
            buffered += len(block)

            while (
                blocks
                and buffered - len(blocks[0]) >= window_samples
            ):
                buffered -= len(blocks.popleft())

            if buffered < int(0.30 * SAMPLE_RATE):
                continue

            window = np.concatenate(tuple(blocks))

            if len(window) > window_samples:
                window = window[-window_samples:]

            result = match(index, window)
            now = time.monotonic()

            if args.debug and now - last_debug >= 0.5:
                print(
                    f"votes={result.votes:4d} "
                    f"runner={result.runner_up:3d} "
                    f"matched={result.matched_hashes:4d}",
                    flush=True,
                )
                last_debug = now

            if (
                is_detection(
                    result,
                    args.threshold,
                    args.ratio,
                )
                and now - last_detection >= args.cooldown
            ):
                events += 1
                execute_action(
                    args.command,
                    events,
                    result,
                )
                last_detection = now

                if args.once:
                    return events

    finally:
        proc.terminate()

        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()

    return events


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Detect one known sound inside a mixed "
            "Linux desktop audio stream."
        )
    )

    parser.add_argument(
        "--fingerprint",
        default="target_fingerprint.json",
    )
    parser.add_argument(
        "--source",
        default="auto",
        help=(
            "Pulse/PipeWire source; default: "
            "monitor of default sink"
        ),
    )
    parser.add_argument(
        "--input-file",
        help="Offline test instead of live capture",
    )
    parser.add_argument(
        "--command",
        default="",
        help="Shell command to run on detection",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=45,
        help="Minimum fingerprint offset votes",
    )
    parser.add_argument(
        "--ratio",
        type=float,
        default=1.6,
        help="Best-vs-runner-up vote ratio",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=0.70,
        help="Rolling analysis window in seconds",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.10,
        help="Analysis step in seconds",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=1.5,
        help="Seconds before another trigger",
    )
    parser.add_argument(
        "--once",
        action="store_true",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
    )

    args = parser.parse_args()
    index, meta = load_index(args.fingerprint)

    print(
        f"fingerprint: {len(index)} unique hashes / "
        f"{meta.get('hash_count', '?')} total"
    )

    if args.input_file:
        audio = decode_audio(args.input_file)
        events = detect_array(
            index,
            audio,
            args,
        )
        print(f"detections: {events}")
        return

    detect_live(index, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
    except Exception as exc:
        print(
            f"error: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
