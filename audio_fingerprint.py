from __future__ import annotations

import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

SAMPLE_RATE = 16_000
FFT_SIZE = 1_024
HOP_SIZE = 256
MIN_HZ = 180
MAX_HZ = 7_600
PEAKS_PER_FRAME = 7
FANOUT = 5
PAIR_MAX_DT = 18


@dataclass(frozen=True)
class MatchResult:
    votes: int
    runner_up: int
    matched_hashes: int
    live_hashes: int
    offset_frames: int | None


def decode_audio(path: str | Path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.decode("utf-8", errors="replace").strip()
            or "ffmpeg failed"
        )
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32, copy=True)


def _spectral_peaks(samples: np.ndarray) -> list[tuple[int, int, float]]:
    samples = np.asarray(samples, dtype=np.float32)
    if samples.size < FFT_SIZE:
        return []

    frames = np.lib.stride_tricks.sliding_window_view(samples, FFT_SIZE)[::HOP_SIZE].copy()
    frames *= np.hanning(FFT_SIZE).astype(np.float32)

    mag = np.abs(np.fft.rfft(frames, axis=1)) + 1e-9
    db = 20.0 * np.log10(mag)

    freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / SAMPLE_RATE)
    lo = int(np.searchsorted(freqs, MIN_HZ))
    hi = int(np.searchsorted(freqs, MAX_HZ))

    out: list[tuple[int, int, float]] = []
    for frame_idx, row in enumerate(db[:, lo:hi]):
        floor = float(np.median(row))
        idx, _ = find_peaks(
            row,
            distance=3,
            prominence=5.0,
            height=floor + 8.0,
        )
        if idx.size == 0:
            continue

        strengths = row[idx]
        top = idx[np.argsort(strengths)[-PEAKS_PER_FRAME:]]

        for local_bin in top:
            out.append(
                (
                    frame_idx,
                    int(local_bin + lo),
                    float(row[local_bin]),
                )
            )

    return out


def fingerprint(
    samples: np.ndarray,
) -> list[tuple[tuple[int, int, int], int]]:
    by_time: dict[int, list[tuple[int, float]]] = defaultdict(list)

    for t, freq_bin, strength in _spectral_peaks(samples):
        by_time[t].append((freq_bin, strength))

    hashes: list[tuple[tuple[int, int, int], int]] = []

    for t in sorted(by_time):
        anchors = sorted(
            by_time[t],
            key=lambda item: item[1],
            reverse=True,
        )[:5]

        candidates: list[tuple[int, int, float]] = []

        for future_t in range(t + 1, t + PAIR_MAX_DT + 1):
            for freq_bin, strength in by_time.get(future_t, ()):
                candidates.append((future_t, freq_bin, strength))

        candidates.sort(key=lambda item: item[2], reverse=True)
        candidates = candidates[:8]

        for f1, _ in anchors:
            for future_t, f2, _ in candidates[:FANOUT]:
                # Coarse quantization makes the hash a little more tolerant
                # to mixer/resampler differences.
                key = (f1 // 2, f2 // 2, future_t - t)
                hashes.append((key, t))

    return hashes


def build_index(
    samples: np.ndarray,
) -> dict[tuple[int, int, int], list[int]]:
    index: dict[tuple[int, int, int], list[int]] = defaultdict(list)

    for key, t in fingerprint(samples):
        index[key].append(t)

    return dict(index)


def match(
    index: dict[tuple[int, int, int], list[int]],
    samples: np.ndarray,
) -> MatchResult:
    votes: Counter[int] = Counter()
    matched = 0
    live = fingerprint(samples)

    for key, live_t in live:
        ref_times = index.get(key)
        if not ref_times:
            continue

        matched += 1

        for ref_t in ref_times:
            votes[ref_t - live_t] += 1

    if not votes:
        return MatchResult(
            votes=0,
            runner_up=0,
            matched_hashes=matched,
            live_hashes=len(live),
            offset_frames=None,
        )

    best = votes.most_common(2)

    return MatchResult(
        votes=best[0][1],
        runner_up=best[1][1] if len(best) > 1 else 0,
        matched_hashes=matched,
        live_hashes=len(live),
        offset_frames=best[0][0],
    )
