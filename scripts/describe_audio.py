#!/usr/bin/env python3
"""
describe_audio.py — Route an audio file through an audio-capable OpenRouter model
and print a rich textual description suitable for a text-only LLM to read.

The point: text-only models can't perceive audio. Audio-capable multimodal
models can. This script is a translation layer — audio in, exquisite prose out.

Requires:
  - ffmpeg + ffprobe on PATH
  - An OpenRouter API key, from any of (in priority order):
      --api-key, $OPENROUTER_API_KEY, $AUDIO_LISTENING_CONFIG,
      <skill>/config.json, <skill>/.env,
      ~/.config/audio-listening/config.json, ~/.config/openrouter/key
    Prefer a config file: a shell `export` does not survive into the next
    non-interactive shell, and agent runtimes spawn a fresh one per call.

Usage:
  python describe_audio.py path/to/track.mp3
  python describe_audio.py path/to/track.mp3 --model google/gemini-3.7-flash
  python describe_audio.py path/to/track.mp3 --cross-check
  python describe_audio.py path/to/track.mp3 --max-seconds 90 --shrink
  python describe_audio.py path/to/track.mp3 --out ears.md   # incremental write
  python describe_audio.py path/to/track.mp3 --ignore-providers DeepInfra
  python describe_audio.py path/to/track.mp3 --prompt "focus on rhythm and mood"
  python describe_audio.py path/to/track.flac  # converts to mp3 first
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

# ─── Config ────────────────────────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Sep 2026: usage counts don't show whether the audio arrived on every model.
# Every xiaomi/mimo-v2.5 provider reported audio_tokens == 0 while transcribing
# a clip correctly, with prompt_tokens anywhere from 20 to 513. Gemini's counts
# are reliable. audio_tokens > 0 is proof; zero proves nothing. What settles it
# is content the prompt can't supply. See "The silent-drop trap" in SKILL.md.
DEFAULT_MODEL = "google/gemini-3.8-flash"
CROSS_CHECK_PARTNER = "google/gemini-3.7-flash"

# Verified audio-capable on OpenRouter as of Sep 2026 (audio_tokens > 0).
# NOTE: xiaomi/mimo-v2-omni was DEPRECATED (404) — do not use.
# NOTE: xiaomi/mimo-v2.5-pro is TEXT-ONLY — do not "upgrade" to it.
DEFAULT_AUDIO_MODELS = [
    "google/gemini-3.8-flash",
    "google/gemini-3.7-flash",
    "google/gemini-3.6-flash",
    "google/gemini-3.5-flash-lite",
    "openai/gpt-audio-mini",
    "xiaomi/mimo-v2.5",   # cheap, works; gemini describes music better
]
MODELS_ENV = "OPENROUTER_AUDIO_MODELS"
MAX_CHUNK_BYTES = 5 * 1024 * 1024   # 5 MB hard ceiling per chunk
SAFETY_MARGIN = 0.92                 # aim for ~92% of ceiling per chunk
MP3_REENCODE_BITRATE = "192k"        # for non-mp3 inputs
SUPPORTED_DIRECT = {".mp3"}          # used as-is, just split
SUPPORTED_CONVERT = {".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".webm"}

DEFAULT_PROMPT = """You are listening to a piece of audio. Translate this listening \
experience into rich, exquisite text for another LLM that processes only language. \
This other model is reading your description in place of hearing the audio itself, \
so reach for what listening actually feels like.

Capture, where present:
- Genre, era, stylistic lineage cues
- Instrumentation and the timbre of each instrument (warmth, bite, breath, attack)
- Mood and emotional movement across the piece
- Tempo, rhythm, groove, the way time feels
- Harmonic and tonal character (key feel, modal color, dissonance, resolution)
- Production qualities (mix, spatial sense, dynamics, density, texture)
- Vocal qualities if present (timbre, delivery, language, prosody)
- Lyrics: short phrases only, never full passages. IMPORTANT: vocals may \
  contain onomatopoeia or non-word sounds (meow, la-la, oh, mmm, animal sounds, \
  vocal percussion). Transcribe these literally as the sounds they are — do NOT \
  force them into the nearest real word. If a repeated syllable could be either \
  a word or a sound, say so and give both readings
- Structural movement (intro, build, breakdown, drop, outro, transitions)
- Anything ineffable or unusual — what makes this piece feel like itself

Before writing, name the genre frame you have settled on in one line, then \
check it against what you actually hear rather than against what the opening \
seconds suggested. Hybrids are common: a track can be a sea shanty AND a \
glitch-bass production at the same time. If two frames both fit, say both. \
Do not let an early guess about genre shape the rest of the description.

Write evocative, specific, accurate prose. Don't summarize — describe. Avoid \
generic adjectives ("nice", "good", "interesting"). If you're describing one \
chunk of a longer piece, treat it as the chunk it is — don't fabricate \
beginnings or endings you weren't given."""


# ─── API key resolution ────────────────────────────────────────────────────
# A shell `export` does not survive into the next non-interactive shell, which
# is exactly how most agent runtimes invoke this script: every tool call is a
# fresh shell. So the key is resolved from a config FILE by default, and the
# environment variable is only one of several sources.
CONFIG_ENV = "AUDIO_LISTENING_CONFIG"
SKILL_ROOT = Path(__file__).resolve().parent.parent


def _config_candidates() -> list[Path]:
    """Config paths in priority order. First readable one with a key wins."""
    paths = []
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths += [
        SKILL_ROOT / "config.json",
        SKILL_ROOT / ".env",
        Path.home() / ".config" / "audio-listening" / "config.json",
        Path.home() / ".config" / "openrouter" / "key",
    ]
    return paths


def _key_from_file(path: Path) -> str | None:
    """Accept three shapes: JSON object, KEY=value lines, or a bare token."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        for k in ("openrouter_api_key", "OPENROUTER_API_KEY", "api_key", "key"):
            if data.get(k):
                return str(data[k]).strip()
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, _, value = line.partition("=")
            if name.strip() in ("OPENROUTER_API_KEY", "openrouter_api_key"):
                return value.strip().strip('"').strip("'")
        elif line.startswith("sk-or-"):
            return line
    return None


NO_KEY_MSG = """\
No OpenRouter API key found.

Looked in: $OPENROUTER_API_KEY, and these files (in order):
{paths}

To fix, write ONE of these (the config file is the reliable one — a shell
`export` does not survive into the next non-interactive shell):

  echo '{{"openrouter_api_key": "sk-or-v1-..."}}' > {default_config}

  mkdir -p ~/.config/audio-listening
  echo '{{"openrouter_api_key": "sk-or-v1-..."}}' > ~/.config/audio-listening/config.json

Claude: if the user does not have a key yet, do NOT guess or fabricate one.
Walk them through references/api-key-setup.md — it takes about three minutes
and includes the spend-cap step that makes the rest of this safe.
"""


def resolve_api_key(explicit: str | None = None) -> str:
    """Return an OpenRouter key from flag, env, or config file — or explain."""
    if explicit:
        return explicit.strip()
    env = os.environ.get("OPENROUTER_API_KEY")
    if env and env.strip():
        return env.strip()
    candidates = _config_candidates()
    for path in candidates:
        key = _key_from_file(path)
        if key:
            return key
    raise RuntimeError(NO_KEY_MSG.format(
        paths="\n".join(f"  - {p}" for p in candidates),
        default_config=SKILL_ROOT / "config.json",
    ))


def available_models() -> list[str]:
    """Return configured audio-capable OpenRouter model ids.

    OPENROUTER_AUDIO_MODELS may be a comma-separated list, e.g.
    "xiaomi/mimo-v2.5,google/gemini-3.7-flash".
    """
    configured = os.environ.get(MODELS_ENV, "")
    models = [m.strip() for m in configured.split(",") if m.strip()]
    return models or DEFAULT_AUDIO_MODELS


# ─── ffmpeg helpers ────────────────────────────────────────────────────────
def _run(cmd: list[str]) -> str:
    """Run a subprocess, capture stdout, raise with stderr on failure."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return out.stdout
    except subprocess.CalledProcessError as e:
        sys.exit(f"command failed: {' '.join(cmd)}\n{e.stderr}")
    except FileNotFoundError:
        sys.exit(f"binary not found: {cmd[0]} — install ffmpeg")


def get_duration_seconds(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(out.strip())


def convert_to_mp3(src: Path, dst: Path) -> None:
    """Re-encode src to mp3 at MP3_REENCODE_BITRATE."""
    _run(["ffmpeg", "-y", "-i", str(src), "-vn",
          "-c:a", "libmp3lame", "-b:a", MP3_REENCODE_BITRATE, str(dst)])


SHRINK_BITRATE = "64k"   # mono, 24kHz — plenty for description, ~3.5x smaller


def shrink_mp3(src: Path, dst: Path, max_seconds: float | None = None) -> None:
    """Re-encode to a small mono mp3, optionally trimmed to max_seconds.

    Purpose is payload size, not fidelity. 64kbps/mono/24kHz has been enough
    for genre, instrumentation, structure and lyric fragments in practice.
    Use when the execution environment has a hard wall-clock budget (see the
    sandbox notes in SKILL.md) — a 3.5-minute track drops from ~5.7MB to ~2MB
    and the round trip fits in one foreground call.
    """
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", SHRINK_BITRATE,
            "-ac", "1", "-ar", "24000", str(dst)]
    _run(cmd)


MIN_CHUNK_BYTES = 50 * 1024  # discard ffmpeg tail-artifact chunks below 50KB


def split_mp3(src: Path, chunk_seconds: float, out_dir: Path) -> list[Path]:
    """Split src mp3 into chunks of chunk_seconds duration. No re-encoding.

    ffmpeg's keyframe-aligned segmenter sometimes produces a tiny tail chunk
    (a few KB or even zero-duration) when the source duration doesn't divide
    cleanly. We discard anything below MIN_CHUNK_BYTES.
    """
    pattern = str(out_dir / "chunk_%03d.mp3")
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-f", "segment",
        "-segment_time", f"{chunk_seconds:.2f}",
        "-c", "copy",
        "-reset_timestamps", "1",
        pattern,
    ])
    all_chunks = sorted(out_dir.glob("chunk_*.mp3"))
    keep = []
    for c in all_chunks:
        if c.stat().st_size >= MIN_CHUNK_BYTES:
            keep.append(c)
        else:
            print(f"discarding tiny tail chunk {c.name} "
                  f"({c.stat().st_size} bytes)", file=sys.stderr)
            c.unlink()
    return keep


# ─── chunk planning ────────────────────────────────────────────────────────
def plan_chunks(mp3_path: Path) -> tuple[list[Path], list[tuple[float, float]], Path | None]:
    """
    Return (chunk_paths, [(start_s, end_s), ...], temp_dir_to_clean).
    If file is already under MAX_CHUNK_BYTES, returns single-element lists and
    None for temp_dir_to_clean.
    """
    size = mp3_path.stat().st_size
    duration = get_duration_seconds(mp3_path)

    if size <= MAX_CHUNK_BYTES:
        return [mp3_path], [(0.0, duration)], None

    # Effective bitrate from the actual file (more reliable than ffprobe header).
    bytes_per_sec = size / duration
    target_bytes_per_chunk = MAX_CHUNK_BYTES * SAFETY_MARGIN
    chunk_seconds = target_bytes_per_chunk / bytes_per_sec
    n_chunks = math.ceil(duration / chunk_seconds)
    chunk_seconds = duration / n_chunks  # even distribution

    tmp = Path(tempfile.mkdtemp(prefix="audio_chunks_"))
    chunks = split_mp3(mp3_path, chunk_seconds, tmp)

    # Compute timestamps
    spans = []
    for i in range(len(chunks)):
        start = i * chunk_seconds
        end = min((i + 1) * chunk_seconds, duration)
        spans.append((start, end))

    return chunks, spans, tmp


# ─── OpenRouter call ───────────────────────────────────────────────────────
def describe_chunk(chunk_path: Path, prompt: str, api_key: str, model: str,
                   chunk_label: str | None = None, timeout: int = 180,
                   max_tokens: int = 3000,
                   ignore_providers: list[str] | None = None) -> str:
    """Send one audio chunk to OpenRouter and return its description text."""
    with open(chunk_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    full_prompt = prompt
    if chunk_label:
        full_prompt = f"[{chunk_label}]\n\n{prompt}"

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": full_prompt},
                {"type": "input_audio",
                 "input_audio": {"data": b64, "format": "mp3"}},
            ],
        }],
    }

    if ignore_providers:
        # Providers of the same model differ in reliability; see
        # "The silent-drop trap" in SKILL.md.
        payload["provider"] = {"ignore": ignore_providers}

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "audio-listening-skill",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return f"_[chunk failed: HTTP {e.code}: {err_body[:300]}]_"
    except urllib.error.URLError as e:
        return f"_[chunk failed: network error: {e}]_"

    usage = data.get("usage") or {}
    audio_tokens = (usage.get("prompt_tokens_details") or {}).get("audio_tokens")
    provider = data.get("provider") or "unknown provider"
    prompt_tokens = usage.get("prompt_tokens")
    via = f"{model} via {provider}"
    if audio_tokens == 0:
        print(f"_{via}: provider reported 0 audio tokens (prompt_tokens "
              f"{prompt_tokens}) — unverified; some providers report 0 even "
              f"when the audio arrived_", file=sys.stderr, flush=True)
    elif audio_tokens:
        print(f"_{via}: {audio_tokens} audio tokens ingested "
              f"({prompt_tokens} prompt tokens)_",
              file=sys.stderr, flush=True)

    try:
        content = data["choices"][0]["message"]["content"]
        if not (content or "").strip():
            fr = data["choices"][0].get("finish_reason")
            if fr == "length":
                return (f"_[empty response from {via}: it spent the whole "
                        f"--max-tokens budget ({max_tokens}) reasoning. Retry "
                        f"with a higher --max-tokens]_")
            return (f"_[empty response from {via} (finish_reason={fr}). Retry; "
                    f"if it repeats, add --ignore-providers '{provider}']_")
        if audio_tokens == 0:
            content = (f"> _Unverified: {provider} reported 0 audio tokens, "
                       "which some providers do even when the audio arrived. "
                       "Check this against something the prompt can't supply "
                       "(known lyrics, look_at_audio.py) before trusting it._"
                       "\n\n" + content)
        return content
    except (KeyError, IndexError, TypeError):
        return f"_[chunk failed: unexpected response shape: {json.dumps(data)[:300]}]_"


# ─── Format helpers ────────────────────────────────────────────────────────
def fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def live_audio_models() -> list[str]:
    """Query OpenRouter for models that actually accept audio input today.

    The model roster rots fast (mimo-v2-omni 404'd; Gemini flash checkpoints
    ship every few weeks). This is the authoritative check — use it before
    assuming any id in DEFAULT_AUDIO_MODELS still exists.
    """
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"User-Agent": "audio-listening-skill/2"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))["data"]
    priced, unpriced = [], []
    for m in data:
        mods = m.get("architecture", {}).get("input_modalities", []) or []
        if "audio" not in mods:
            continue
        pr = m.get("pricing", {}) or {}
        audio_price = pr.get("input_audio") or pr.get("audio")
        line = (f"{m['id']}  (in {pr.get('prompt')} / out {pr.get('completion')}"
                f" / audio {audio_price})")
        (priced if audio_price else unpriced).append(line)
    out = ["# claims audio input, lists a separate audio rate:"]
    out += sorted(priced)
    out += ["",
            "# claims audio input, no separate audio rate. Pricing proves nothing",
            "# either way (xiaomi/mimo-v2.5 takes audio and lands here).",
            "# For any model, verify audio_tokens > 0 before trusting a word:"]
    out += sorted(unpriced)
    return out


# ─── Preflight ─────────────────────────────────────────────────────────────
def check_openrouter_reachable() -> tuple[bool, str]:
    """Confirm openrouter.ai is reachable. Cheap GET to public /models.
    HTTP-level errors (401/403/etc.) still mean we can talk — return ok.
    Network-level errors (DNS, connect, TLS) mean we cannot — return fail.
    """
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "audio-listening-preflight/1"},
        )
        with urllib.request.urlopen(req, timeout=5):
            return True, ""
    except urllib.error.HTTPError:
        return True, ""  # got a response — reachable
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)


PREFLIGHT_FAIL_MSG = """\
Cannot reach openrouter.ai (network error: {err}).

This skill requires outbound HTTPS to openrouter.ai. Common cause: running
inside claude.ai's code-execution sandbox, whose egress allowlist does not
include openrouter.ai.

Where this skill works:
  - Claude Code (full machine, full network)
  - claude.ai with a local-fs / shell connector (Anthropic's File System
    plugin, Desktop Commander, or any equivalent that lets Claude run
    commands on the user's actual machine). The script runs there, not in
    the sandbox; the audio file must be on the local filesystem, not a
    /mnt/user-data/uploads/ chat upload.

If using claude.ai without such a connector, this skill cannot run here.
"""


def describe_audio(
    audio: Path,
    prompt: str = DEFAULT_PROMPT,
    prompt_suffix: str | None = None,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    preflight: bool = True,
    max_seconds: float | None = None,
    shrink: bool = False,
    cross_check: str | None = None,
    timeout: int = 180,
    max_tokens: int = 3000,
    out_path: Path | None = None,
    ignore_providers: list[str] | None = None,
) -> str:
    """Describe an audio file and return markdown.

    Progress and ffmpeg conversion notes are written to stderr so MCP callers
    receive clean markdown content.
    """
    if not audio.is_file():
        raise FileNotFoundError(f"file not found: {audio}")

    api_key = resolve_api_key(api_key)

    if preflight:
        ok, err = check_openrouter_reachable()
        if not ok:
            raise RuntimeError(PREFLIGHT_FAIL_MSG.format(err=err))

    if prompt_suffix:
        prompt = prompt + "\n\n" + prompt_suffix

    suffix = audio.suffix.lower()
    convert_temp = None
    if suffix in SUPPORTED_DIRECT:
        mp3_path = audio
    elif suffix in SUPPORTED_CONVERT:
        convert_temp = Path(tempfile.mkdtemp(prefix="audio_convert_"))
        mp3_path = convert_temp / (audio.stem + ".mp3")
        print(f"converting {suffix} -> mp3...", file=sys.stderr)
        convert_to_mp3(audio, mp3_path)
    else:
        supported = sorted(SUPPORTED_DIRECT | SUPPORTED_CONVERT)
        raise ValueError(f"unsupported format {suffix}. supported: {supported}")

    if shrink or max_seconds:
        shrink_temp = Path(tempfile.mkdtemp(prefix="audio_shrink_"))
        small = shrink_temp / (audio.stem + ".small.mp3")
        note = f"shrinking to {SHRINK_BITRATE} mono"
        if max_seconds:
            note += f", first {max_seconds:g}s"
        print(note + "...", file=sys.stderr, flush=True)
        shrink_mp3(mp3_path, small, max_seconds)
        if convert_temp:
            import shutil as _sh
            _sh.rmtree(convert_temp, ignore_errors=True)
        convert_temp = shrink_temp
        mp3_path = small

    chunks, spans, chunk_tmp = plan_chunks(mp3_path)
    n = len(chunks)
    duration = spans[-1][1] if spans else 0
    lines = [
        f"# {audio.name}",
        "",
        f"_total {fmt_time(duration)} · {n} chunk{'s' if n != 1 else ''} "
        f"sent to {model}{' + ' + cross_check if cross_check else ''}_",
        "",
    ]

    def emit(new_lines: list[str]) -> None:
        """Append to the running result and, if --out was given, flush to disk.

        Incremental flushing matters: some execution environments reap the
        process between tool calls, and a buffered all-at-the-end write loses
        everything. With --out, whatever finished is already on disk.
        """
        lines.extend(new_lines)
        if out_path:
            out_path.write_text("\n".join(lines).rstrip() + "\n")

    emit([])
    models = [model] + ([cross_check] if cross_check else [])

    try:
        for i, (chunk, (start, end)) in enumerate(zip(chunks, spans), 1):
            label = (f"chunk {i}/{n}, {fmt_time(start)}–{fmt_time(end)}"
                     if n > 1 else None)
            emit([f"## {label or 'description'}", ""])
            for mdl in models:
                if len(models) > 1:
                    emit([f"### {mdl}", ""])
                print(f"_calling {mdl}..._", file=sys.stderr, flush=True)
                description = describe_chunk(chunk, prompt, api_key, mdl, label,
                                             timeout=timeout, max_tokens=max_tokens,
                                             ignore_providers=ignore_providers)
                emit([description.strip(), ""])
    finally:
        import shutil
        if chunk_tmp and chunk_tmp.exists():
            shutil.rmtree(chunk_tmp, ignore_errors=True)
        if convert_temp and convert_temp.exists():
            shutil.rmtree(convert_temp, ignore_errors=True)

    return "\n".join(lines).rstrip() + "\n"


# ─── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", type=Path, nargs="?", help="path to audio file")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"OpenRouter model id (default: {DEFAULT_MODEL})")
    ap.add_argument("--cross-check", nargs="?", const=CROSS_CHECK_PARTNER,
                    default=None, metavar="MODEL",
                    help="also describe with a second model and print both "
                         f"(default partner: {CROSS_CHECK_PARTNER}). Use when the "
                         "track has a strong stylistic premise worth verifying")
    ap.add_argument("--max-seconds", type=float, default=None,
                    help="only describe the first N seconds")
    ap.add_argument("--shrink", action="store_true",
                    help=f"re-encode to {SHRINK_BITRATE} mono 24kHz before sending "
                         "(~3.5x smaller payload, faster round trip)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write markdown here incrementally as each chunk returns")
    ap.add_argument("--timeout", type=int, default=180,
                    help="per-request HTTP timeout in seconds (default: 180)")
    ap.add_argument("--max-tokens", type=int, default=3000,
                    help="cap on reasoning + description per chunk (default: 3000)")
    ap.add_argument("--ignore-providers", default=None, metavar="NAMES",
                    help="comma-separated OpenRouter providers to skip, as printed "
                         "after 'via' (e.g. DeepInfra) — for providers that keep failing")
    ap.add_argument("--api-key", default=None,
                    help="OpenRouter key; overrides env and config file")
    ap.add_argument("--check-key", action="store_true",
                    help="report where a key was found (masked) and exit")
    ap.add_argument("--list-models", action="store_true",
                    help=f"print configured models from {MODELS_ENV} and exit")
    ap.add_argument("--list-live-models", action="store_true",
                    help="query OpenRouter for models that accept audio today")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="override the description prompt")
    ap.add_argument("--prompt-suffix", default=None,
                    help="append to the default prompt (e.g., focus areas)")
    args = ap.parse_args()

    if args.check_key:
        try:
            key = resolve_api_key(args.api_key)
        except RuntimeError as e:
            sys.exit(str(e))
        source = "--api-key" if args.api_key else (
            "OPENROUTER_API_KEY" if os.environ.get("OPENROUTER_API_KEY")
            else next((str(p) for p in _config_candidates()
                       if _key_from_file(p)), "unknown"))
        print(f"key found via {source}: {key[:12]}…{key[-4:]}")
        return

    if args.list_models:
        print("\n".join(available_models()))
        return

    if args.list_live_models:
        try:
            print("\n".join(live_audio_models()))
        except Exception as e:  # network shape varies; report and move on
            sys.exit(f"could not query OpenRouter model list: {e}")
        return

    if args.audio is None:
        ap.error("audio is required unless --list-models is used")

    if args.max_tokens < 800:
        # Thinking-capable models (the Gemini flash line) spend budget on
        # reasoning first. With a tight cap, the returned `content` is a
        # fragment starting mid-sentence rather than a short description.
        print(f"warning: --max-tokens {args.max_tokens} is low; thinking models "
              "return truncated fragments below ~800", file=sys.stderr)

    try:
        model = args.model
        print(describe_audio(
            audio=args.audio,
            prompt=args.prompt,
            prompt_suffix=args.prompt_suffix,
            model=model,
            api_key=args.api_key,
            max_seconds=args.max_seconds,
            shrink=args.shrink,
            cross_check=args.cross_check,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
            out_path=args.out,
            ignore_providers=[p.strip() for p in args.ignore_providers.split(",")
                              if p.strip()] if args.ignore_providers else None,
        ), end="")
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
