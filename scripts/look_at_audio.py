#!/usr/bin/env python3
"""
look_at_audio.py — Render audio as images Claude can see natively, plus
hard loudness numbers. The second channel of the audio-listening skill.

Why this exists
---------------
describe_audio.py routes audio through an audio-capable model and returns
someone else's *experience* of the sound, in words. Rich, but second-hand,
and it can confabulate (see "The silent-drop trap" and "Onomatopoeia
warning" in SKILL.md).

This script does the opposite. It converts the audio into PNGs, which Claude
ingests with its own vision — no relay, no interpretation, nothing invented.
The pictures carry no experience at all, but they cannot lie about structure.

The two channels fail in opposite directions. The relay hallucinates content;
the picture has no content to hallucinate. Where they agree, you have
something. Where they disagree, the picture wins on anything physical
(level, spectrum, timing, silence) and the relay wins on anything semantic
(words, mood, genre, who is singing).

Requires:
  - ffmpeg + ffprobe on PATH
  - NO API key, NO network. Runs anywhere.

Usage:
  python look_at_audio.py track.mp3
  python look_at_audio.py track.mp3 --outdir looks/
  python look_at_audio.py *.mp3 --metrics-only      # comparison table
  python look_at_audio.py track.mp3 --spectrum-mode separate
  python look_at_audio.py track.mp3 --zoom 60:90    # window in seconds
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".aiff"}


# ─── ffmpeg plumbing ───────────────────────────────────────────────────────

def need(binary: str) -> None:
    if shutil.which(binary) is None:
        sys.exit(f"error: {binary} not found on PATH. Install ffmpeg.")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def duration_of(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def sample_rate_of(path: Path) -> int | None:
    p = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate",
             "-of", "default=nw=1:nk=1", str(path)])
    try:
        return int(p.stdout.split()[0])
    except (ValueError, IndexError):
        return None


# ─── The ebur128 trap ──────────────────────────────────────────────────────
# `-v error` SUPPRESSES the ebur128 summary block. The filter writes its
# summary at info level, so quieting ffmpeg quiets the very thing you asked
# for — and you get empty fields with a zero exit code, which looks like a
# parsing bug and is not. Use `-hide_banner -nostats` instead: that keeps
# stderr readable (no per-frame spam, no banner) while leaving info-level
# filter output intact.

def loudness(path: Path) -> dict:
    """Integrated loudness, loudness range, true peak. EBU R128."""
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-filter_complex", "ebur128=peak=true", "-f", "null", "-"])
    text = p.stderr
    out = {"I": None, "LRA": None, "TP": None}

    m = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+) LUFS", text)
    if m:
        out["I"] = float(m.group(1))
    m = re.search(r"Loudness range:\s*\n\s*LRA:\s*(-?[\d.]+) LU", text)
    if m:
        out["LRA"] = float(m.group(1))
    m = re.search(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+) dBFS", text)
    if m:
        out["TP"] = float(m.group(1))
    return out


def _mean_db(path: Path, afilter: str) -> float | None:
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", afilter, "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr)
    return float(m.group(1)) if m else None


def _steep_hp(freq: int, stages: int = 4) -> str:
    return ",".join([f"highpass=f={freq}:poles=2"] * stages)


def spectral_ceiling(path: Path) -> float | None:
    """
    Lossy-codec lowpass detector. Returns energy above 19 kHz *relative to*
    the full mix, in dB. Below about -55 means a brick wall — a codec
    ceiling baked into the source (Suno, MP3, AAC), not a mixing choice.

    Do NOT do this with a single `highpass=f=19000`. A one-pole filter has a
    slope so gentle it passes a pile of sub-19k energy, and you measure
    filter leakage instead of content: a file with a hard wall at 18 kHz
    reads about -53 dB, which looks like real high-end. Four cascaded
    two-pole stages give a steep enough skirt to trust. Verified Sep 2026.
    """
    sr = sample_rate_of(path)
    if sr is not None and sr < 40000:
        # Nyquist is below ~20 kHz, so there is nothing above 19 kHz to
        # measure. ffmpeg doesn't refuse the filter; it reads 0 dB, which
        # looks like full high end. Report unknown instead.
        return None
    full = _mean_db(path, "volumedetect")
    hi = _mean_db(path, _steep_hp(19000) + ",volumedetect")
    if full is None or hi is None:
        return None
    return hi - full


# ─── Image rendering ───────────────────────────────────────────────────────

def spectrogram(path: Path, out: Path, size: str, mode: str,
                zoom: str | None) -> Path:
    pre = ["-ss", zoom.split(":")[0], "-to", zoom.split(":")[1]] if zoom else []
    lav = (f"showspectrumpic=s={size}:mode={mode}:legend=1"
           f":scale=log:color=viridis")
    p = run(["ffmpeg", "-v", "error", *pre, "-i", str(path),
             "-lavfi", lav, "-y", str(out)])
    if p.returncode != 0:
        sys.exit(f"spectrogram failed:\n{p.stderr}")
    return out


def waveform(path: Path, out: Path, size: str, color: str,
             zoom: str | None) -> Path:
    pre = ["-ss", zoom.split(":")[0], "-to", zoom.split(":")[1]] if zoom else []
    p = run(["ffmpeg", "-v", "error", *pre, "-i", str(path),
             "-filter_complex", f"showwavespic=s={size}:colors={color}",
             "-frames:v", "1", "-y", str(out)])
    if p.returncode != 0:
        sys.exit(f"waveform failed:\n{p.stderr}")
    return out


# ─── Reporting ─────────────────────────────────────────────────────────────

def read_lra(lra: float | None) -> str:
    if lra is None:
        return ""
    if lra < 3.0:
        return "brick — nothing plays alone anywhere"
    if lra < 5.0:
        return "flat — texture changes, level doesn't"
    if lra < 7.0:
        return "some contour — likely one sparse passage"
    return "genuinely dynamic — something plays unaccompanied"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Render audio as images Claude can see, plus loudness numbers.")
    ap.add_argument("files", nargs="+", help="audio files (globs ok)")
    ap.add_argument("--outdir", default="looks", help="where PNGs go")
    ap.add_argument("--metrics-only", action="store_true",
                    help="skip images, just print the comparison table")
    ap.add_argument("--no-wave", action="store_true", help="skip waveforms")
    ap.add_argument("--no-spectrum", action="store_true", help="skip spectrograms")
    ap.add_argument("--spectrum-size", default="1400x800")
    ap.add_argument("--wave-size", default="1400x400")
    ap.add_argument("--spectrum-mode", default="combined",
                    choices=["combined", "separate"],
                    help="separate = one panel per channel, shows stereo width")
    ap.add_argument("--wave-color", default="cyan")
    ap.add_argument("--zoom", default=None, metavar="START:END",
                    help="render only this window, in seconds (e.g. 60:90)")
    args = ap.parse_args()

    need("ffmpeg")
    need("ffprobe")

    paths: list[Path] = []
    for pattern in args.files:
        hits = [Path(h) for h in glob.glob(pattern)] or [Path(pattern)]
        for h in hits:
            if h.suffix.lower() in AUDIO_EXTS and h.exists():
                paths.append(h)
    if not paths:
        sys.exit("error: no readable audio files matched.")
    paths.sort()

    outdir = Path(args.outdir)
    if not args.metrics_only:
        outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    made: list[str] = []

    for p in paths:
        dur = duration_of(p)
        ld = loudness(p)
        hf = spectral_ceiling(p)
        rows.append((p.stem, dur, ld, hf))

        if args.metrics_only:
            continue

        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", p.stem)[:60]
        if not args.no_spectrum:
            made.append(str(spectrogram(
                p, outdir / f"{stem}.spectrum.png",
                args.spectrum_size, args.spectrum_mode, args.zoom)))
        if not args.no_wave:
            made.append(str(waveform(
                p, outdir / f"{stem}.wave.png",
                args.wave_size, args.wave_color, args.zoom)))

    # ── table ──
    print()
    print(f"{'track':<44} {'dur':>7} {'I/LUFS':>8} {'LRA':>6} "
          f"{'TP':>6} {'>19k':>6}  reading")
    print("-" * 112)
    for stem, dur, ld, hf in rows:
        mm, ss = divmod(int(dur), 60)
        print(f"{stem[:44]:<44} {mm:>4}:{ss:02d} "
              f"{ld['I'] if ld['I'] is not None else '?':>8} "
              f"{ld['LRA'] if ld['LRA'] is not None else '?':>6} "
              f"{ld['TP'] if ld['TP'] is not None else '?':>6} "
              f"{round(hf) if hf is not None else '?':>6}  "
              f"{read_lra(ld['LRA'])}")

    # ── flags worth surfacing without being asked ──
    print()
    notes = []
    for stem, dur, ld, hf in rows:
        if ld["TP"] is not None and ld["TP"] > -0.1:
            notes.append(f"  clipping: {stem} peaks at {ld['TP']:+.1f} dBFS")

    ceil = [r[0] for r in rows if r[3] is not None and r[3] < -48]
    if ceil:
        if len(ceil) == len(rows) and len(rows) > 1:
            notes.append(f"  likely lossy ceiling on all {len(rows)} tracks "
                         f"(see the >19k column) — probably one shared encoder")
        else:
            notes.append("  likely lossy ceiling: " + ", ".join(ceil[:6])
                         + ("  ..." if len(ceil) > 6 else ""))
        notes.append("    heuristic only — confirm on a spectrogram, where a "
                     "brick wall is unmistakable")

    if len(rows) > 2:
        vals = [r[2]["I"] for r in rows if r[2]["I"] is not None]
        if vals and (max(vals) - min(vals)) < 3.0:
            notes.append(f"  uniform loudness: all {len(vals)} tracks within "
                         f"{max(vals) - min(vals):.1f} LU — platform "
                         f"normalization, not a mixing choice")
    print("\n".join(notes) if notes else "  (no flags)")

    if made:
        print("\nimages written — view them, don't just trust this table:")
        for m in made:
            print(f"  {m}")
    print()


if __name__ == "__main__":
    main()
