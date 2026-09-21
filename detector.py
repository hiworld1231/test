#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
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

    return index


def run_text(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"{' '.join(cmd)} failed")

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

    monitors = [source for source in sources if source.endswith(".monitor")]
    if monitors:
        return monitors[0]

    raise RuntimeError(
        "no Pulse/PipeWire monitor source found; try: pactl list short sources"
    )


def execute_action(command: str, event_no: int, result) -> None:
    now = time.strftime("%H:%M:%S")
    print(
        f"[{now}] DETECTED #{event_no} "
        f"(votes={result.votes}, runner={result.runner_up})",
        flush=True,
    )

    if not command:
        return

    env = os.environ.copy()
    env.update(
        {
            "SOUND_MATCH_VOTES": str(result.votes),
            "SOUND_MATCH_RUNNER_UP": str(result.runner_up),
            "SOUND_MATCHED_HASHES": str(result.matched_hashes),
            "SOUND_DETECTION_COUNT": str(event_no),
        }
    )

    subprocess.Popen(command, shell=True, env=env)


def is_detection(result, threshold: int, ratio: float) -> bool:
    if result.votes < threshold:
        return False

    return result.runner_up == 0 or result.votes >= result.runner_up * ratio


def detect_array(index, audio: np.ndarray, args) -> int:
    window_samples = int(args.window * SAMPLE_RATE)
    step_samples = max(1, int(args.step * SAMPLE_RATE))
    first_end = max(int(0.30 * SAMPLE_RATE), step_samples)

    events = 0
    armed = True
    quiet_since: float | None = None
    last_detection = -1e9

    for end in range(first_end, len(audio) + step_samples, step_samples):
        chunk = audio[
            max(0, end - window_samples):
            min(end, len(audio))
        ]

        if len(chunk) < int(0.30 * SAMPLE_RATE):
            continue

        result = match(index, chunk)
        now = end / SAMPLE_RATE
        detected = is_detection(result, args.threshold, args.ratio)

        if detected:
            quiet_since = None

            if armed and now - last_detection >= args.cooldown:
                events += 1
                execute_action(args.command, events, result)
                last_detection = now
                armed = False

                if args.once:
                    return events
        else:
            if quiet_since is None:
                quiet_since = now

            if not armed and now - quiet_since >= args.rearm:
                armed = True

    return events


def detect_live(index, args) -> int:
    if not shutil.which("parec"):
        raise RuntimeError(
            "parec not found (on Arch/CachyOS: sudo pacman -S libpulse)"
        )

    source = pulse_monitor_source(args.source)

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

    step_samples = max(1, int(args.step * SAMPLE_RATE))
    window_samples = max(step_samples, int(args.window * SAMPLE_RATE))

    blocks = deque()
    buffered = 0
    events = 0

    # One physical playback should count once even if several consecutive
    # analysis windows match it.
    armed = True
    quiet_since: float | None = None
    last_detection = -1e9

    try:
        while True:
            raw = proc.stdout.read(step_samples * 4)

            if not raw:
                stderr = (
                    proc.stderr.read().decode("utf-8", errors="replace")
                    if proc.stderr
                    else ""
                )
                raise RuntimeError(stderr.strip() or "parec stopped")

            block = np.frombuffer(raw, dtype="<f4").astype(np.float32, copy=True)
            blocks.append(block)
            buffered += len(block)

            while blocks and buffered - len(blocks[0]) >= window_samples:
                buffered -= len(blocks.popleft())

            if buffered < int(0.30 * SAMPLE_RATE):
                continue

            window = np.concatenate(tuple(blocks))
            if len(window) > window_samples:
                window = window[-window_samples:]

            result = match(index, window)
            now = time.monotonic()
            detected = is_detection(result, args.threshold, args.ratio)

            if detected:
                quiet_since = None

                if armed and now - last_detection >= args.cooldown:
                    events += 1
                    execute_action(args.command, events, result)
                    last_detection = now
                    armed = False

                    if args.once:
                        return events
            else:
                if quiet_since is None:
                    quiet_since = now

                if not armed and now - quiet_since >= args.rearm:
                    armed = True

    except KeyboardInterrupt:
        return events
    finally:
        proc.terminate()

        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect one known sound inside a mixed Linux audio stream."
    )

    parser.add_argument("--fingerprint", default="target_fingerprint.json")
    parser.add_argument(
        "--source",
        default="auto",
        help="Pulse/PipeWire source; default: monitor of default sink",
    )
    parser.add_argument("--input-file", help="Offline test instead of live capture")
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
        default=1.0,
        help="Absolute minimum seconds between detections",
    )
    parser.add_argument(
        "--rearm",
        type=float,
        default=0.45,
        help="How long the target must be absent before another detection is allowed",
    )
    parser.add_argument("--once", action="store_true")

    args = parser.parse_args()
    index = load_index(args.fingerprint)

    if args.input_file:
        audio = decode_audio(args.input_file)
        return detect_array(index, audio, args)

    return detect_live(index, args)


def _interrupt(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _interrupt)
        except (ValueError, OSError):
            pass

    try:
        count = main()
        print(f"Detected total: {count}", flush=True)
    except KeyboardInterrupt:
        # Fallback for an interrupt before live capture is fully initialized.
        print("Detected total: 0", flush=True)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
