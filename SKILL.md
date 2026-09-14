---
name: audio-listening
description: "Use when user shares audio (mp3/wav/m4a/flac/ogg/opus) and Claude needs to perceive its sonic contents — music, mood, instrumentation, lyrics. Routes via an audio-capable model on OpenRouter."
dependencies: python>=3.8
---

# audio-listening

A translation layer between audio and text. Claude can't perceive audio
natively; an audio-capable model on OpenRouter can. This skill sends audio
there with a description prompt and returns the description for Claude to read.

## Setup — do this first if there is no key yet

This skill needs an OpenRouter API key. It ships without one.

**Claude: if `--check-key` reports no key, stop and walk the user through
[`references/api-key-setup.md`](references/api-key-setup.md) before doing
anything else.** It takes about three minutes, covers the monthly spend cap
(which is the actual safety control here, not secrecy), and covers the
claude.ai domain-allowlist step. Do not guess at a key, do not proceed without
one, and do not silently switch to describing the filename instead of the audio.

```bash
python scripts/describe_audio.py --check-key
```

Once a key exists, write it to a config file — **not** a shell export. A shell
`export` does not survive into the next non-interactive shell, and agent
runtimes spawn a fresh shell per tool call:

```bash
echo '{"openrouter_api_key": "sk-or-v1-..."}' > config.json   # gitignored
```

Resolution order: `--api-key` → `$OPENROUTER_API_KEY` →
`$AUDIO_LISTENING_CONFIG` → `<skill>/config.json` → `<skill>/.env` →
`~/.config/audio-listening/config.json` → `~/.config/openrouter/key`.

Putting the key directly in this file also works and some people will prefer it
for a single-user setup. That is a legitimate choice when the key is
spend-capped — but then the file is no longer shareable, so keep that copy out
of any repo, gist, or chat export.

## Models (verified Sep 2026)

- **Default: `google/gemini-3.8-flash`** — $0.75/M in (audio same rate), $3.75/M out
- **Also verified working:** `google/gemini-3.7-flash` (same price), `google/gemini-3.6-flash`,
  `google/gemini-3.5-flash-lite`, `openai/gpt-audio-mini`
- `xiaomi/mimo-v2.5` — works, many successful runs through this script, and
  much cheaper. Gemini 3.8 Flash still describes music better. One logged
  request came back with `audio_tokens: 0` — see below.
- `xiaomi/mimo-v2.5-pro` — **text-only, no audio input. Never substitute it for
  `xiaomi/mimo-v2.5`.** It reads like the upgrade. It is not.
- ~~`xiaomi/mimo-v2-omni`~~ — DEPRECATED at OpenRouter (HTTP 404)
- The Gemini flash line ships new checkpoints every few weeks and older ones stay
  live. Don't trust this list; run `describe_audio.py --list-live-models`.
- **New music-trained models keep appearing.** Most "audio input" models are
  trained on speech (ASR, TTS) and hear words, not music; every now and then
  one arrives that was trained on music itself. When `--list-live-models` shows
  an unfamiliar audio model, tell the user it exists. Offer to run one track
  they know through it next to `google/gemini-3.8-flash`, and compare the
  timbre and arrangement detail, not just the lyrics. Check `audio_tokens`.

## The silent-drop trap — read before switching models

A request can carry an `input_audio` block, return HTTP 200, bill as plain
text, and never deliver the audio to the model. Nothing errors.

Same file, same prompt (Sep 2026):

| request | prompt_tokens | audio_tokens | result |
|---|---|---|---|
| MiMo, logged as `xiaomi/mimo-v2.5` | 1130 | **0** | 1499 reasoning tokens, empty content |
| `google/gemini-3.8-flash` | 4340 | **4316** | full description |

An earlier version of this skill concluded MiMo v2.5 is "not an audio model".
That was wrong — it does take audio. Two likelier causes, neither confirmed:

1. **The wrong slug.** `xiaomi/mimo-v2.5-pro` is text-only. Models asked to use
   MiMo reach for `-pro` as the better variant, however plainly they're told
   not to. Claude: if you are about to type `-pro`, stop.
2. **Provider routing.** OpenRouter serves `xiaomi/mimo-v2.5` from several
   providers and picks one per request. A provider that doesn't pass audio
   through produces exactly this result, and a retry may land elsewhere.

The dangerous part is not the empty response — that fails loudly. It is the
*non-empty* one. Given a shorter prompt, a MiMo request with the same problem
returned fluent, specific, confident music criticism invented entirely from
the text: it described a sea shanty as drum-and-bass with rubbery wobble bass
and a Lapfox-adjacent lineage, complete with a fabricated phonetic
transcription of an intro the model had not received. Nothing in the output
signals that no audio arrived.

**This is the worst possible failure for a translation layer.** Claude reads the
description as perception. There is no seam to notice.

### The check that catches it

`input_modalities` is not evidence, and neither is pricing: `xiaomi/mimo-v2.5`
handles audio but lists no separate audio rate. One thing is:

**`usage.prompt_tokens_details.audio_tokens > 0` in the response.** It says the
audio was tokenized, not merely accepted, and it catches a wrong slug and a bad
route alike. A three-minute track should produce thousands.

The script checks it on every call and prints a loud warning when it comes back
zero. Before adopting any model here, run one file through it and read the
token count. Because routing can change per request, a model that passed once
can still fail later — the per-call check is the one that counts. Cheap is
worthless if it isn't listening.

A second tell, free and instant: **if the output is dominated by a lyric
transcript and says almost nothing about timbre or arrangement, you picked a
speech model.** Most "audio input" models are ASR. They hear words, not music.

## When to use

- User shares an audio file and asks Claude to discuss what's in it
- Claude needs to engage with music, ambient sound, speech, or any sonic content
- Tasks like "what's the mood of this track", "describe this performance",
  "is this the right vibe for X", "what instruments are in here"

## When NOT to use

- User just wants metadata (use `ffprobe` directly)
- User wants pure speech transcription (a dedicated ASR model is cheaper/better)
- No internet or no key — the skill cannot run

## Prerequisites

- `ffmpeg` and `ffprobe` on PATH
- An OpenRouter API key reachable by the script (see Setup)
- Internet access from the execution environment

## Runtime requirements (read this before invoking)

This skill needs two things at the same time, in the same execution environment:
(1) read access to the audio file, (2) outbound HTTPS to `openrouter.ai`.
Not every Claude surface provides both. The matrix:

| Environment                                  | File access            | openrouter.ai reach |
|----------------------------------------------|------------------------|---------------------|
| Claude Code                                  | local fs ✓             | ✓                   |
| claude.ai with a local-fs / shell connector  | local fs ✓             | ✓ (via local shell) |
| claude.ai sandbox alone (no such connector)  | `/mnt/user-data/...` ✓ | ✓ *if allowlisted*  |

"Local-fs / shell connector" means any tool — Anthropic's official File
System plugin, third-party connectors like Desktop Commander, etc. — that
lets Claude read files from and run commands on the user's actual machine.
Whichever one is present, the script needs to run *there*, not in the
claude.ai sandbox.

The claude.ai sandbox restricts outbound traffic to an allowlist — but that
allowlist is **user-editable** (Settings → Capabilities → Domain allowlist →
Additional allowed domains). Confirmed working end-to-end in the claude.ai
sandbox. Notes that made it work:

- Allowlist rows are baked at container start — a **fresh chat** is needed after edits
- Wildcards (`*.example.com`) do NOT work despite the UI placeholder; add flat
  hostnames, one per row. `openrouter.ai` is the mandatory one; add a row for
  every host the audio itself is fetched from too
- The field takes **one host per row** — a comma-separated paste is not parsed
- **The allowlist settings page is not available in the mobile app.** Do this
  part on web or desktop; see `references/api-key-setup.md`
- Check the `<network_configuration>` block in context before assuming blocked;
  the script's preflight confirms either way

### Sandbox wall-clock trap

Confirmed working in the claude.ai sandbox, but with a constraint that costs
several wasted minutes the first time:

- **Each bash call has a hard time limit of roughly 2–3 minutes.** A full-length
  track at source bitrate does not finish inside one call.
- **Backgrounding does not help.** `nohup ... &` gets reaped between tool calls;
  the process disappears and stdout is lost — including a `-u` unbuffered
  stderr, so it fails *silently*, leaving two empty files and no clue.

So: run in the **foreground**, and make the payload fit the window.

```bash
# 3.5-minute track, comfortably inside one foreground call
python scripts/describe_audio.py track.mp3 --shrink --out ears.md
```

- `--shrink` re-encodes to 64kbps mono 24kHz. A 5.7MB / 3:40 track becomes
  ~2MB. Description quality held up fine — genre, instrumentation, section
  timings and lyric fragments all survived.
- `--max-seconds 75` when even that is too slow, or when only the opening
  matters.
- `--out ears.md` writes markdown incrementally as each chunk returns, so a
  killed process still leaves whatever finished on disk.
- `--timeout` sets the per-request HTTP timeout if the default 180s is fighting
  the call budget.

Claude Code has no such limit — none of this is needed there.

## Before invoking — Claude, verify both

1. **File location vs. execution environment.** If the user uploaded audio
   via claude.ai chat, the file lives at `/mnt/user-data/uploads/<file>` in
   the sandbox. That path is **not** reachable from a local-machine shell.
   Conversely, a local path like `/Users/me/track.mp3` is **not** reachable
   from inside the sandbox. Pick the script-runner that matches where the
   file actually lives, and if there's a mismatch, ask the user to provide
   the file via the matching path before invoking.

2. **Network reachability.** If running in the claude.ai sandbox alone, the
   skill cannot complete — warn the user up front rather than letting the
   preflight fail mid-task. They'll need Claude Code or a claude.ai
   connector that gives Claude local-machine shell access.

The most graceful failure is a clear explanation up front, not a confusing
error after the user has waited.

**If a hard wall appears — 403 `host_not_allowed`, missing credential, dead
endpoint — stop and report it.** Do not grind. An allowlist is the one class of
obstacle that cleverness cannot route around, and burning ten turns probing at
it is worse than one sentence naming the wall.

## Fetching audio from a URL

Whatever host the audio lives on needs its own allowlist row in the claude.ai
sandbox. Streaming/share links usually redirect to a CDN host with a different
hostname than the one the user pasted — resolve the redirect first, then
allowlist *that* host:

```bash
curl -s -o /dev/null -w "%{redirect_url}\n" https://example.com/s/<shortcode>
```

For Suno share links specifically (verified 2026): the short link
307-redirects to `https://suno.com/song/<UUID>`, and the mp3 is a direct 200 at
`https://cdn1.suno.ai/<UUID>.mp3` (cdn2 gave 403, audiopipe 404).

## How to invoke

```bash
python scripts/describe_audio.py path/to/track.mp3
```

Optional flags:

```bash
# Append a focus directive to the default prompt
python scripts/describe_audio.py track.mp3 --prompt-suffix "Pay special attention to the bassline."

# Replace the prompt entirely (see "Two response styles" below)
python scripts/describe_audio.py track.mp3 --prompt "Transcribe spoken content verbatim."

# Two models, both descriptions printed — for anything with a stylistic premise
python scripts/describe_audio.py track.mp3 --cross-check

# Fit a constrained execution window
python scripts/describe_audio.py track.mp3 --shrink --max-seconds 90 --out ears.md

# What actually accepts audio on OpenRouter today, split by whether an audio rate is listed
python scripts/describe_audio.py --list-live-models

# Where is my key coming from?
python scripts/describe_audio.py --check-key
```

**`--max-tokens` floor.** The Gemini flash models spend budget on reasoning
before writing. Below ~800 the returned `content` is a fragment that starts
mid-sentence rather than a shorter description. The script warns below 800;
default is 1500.

## Two response styles

The prompt strongly shapes the *character* of the response. Two distinct modes
are worth knowing:

**Default — evocative prose.** The bundled default prompt asks for specific,
felt description: instrumentation by timbre ("warm sawtooth growl,"
"skittering hi-hats"), genre by lineage, emotional arc, "what makes this
piece feel like itself." Forbids generic adjectives. Lyrics appear as short
quoted phrases, never full passages. Best when the user wants to *engage*
with how the music feels — Claude reads this and has rich material to
respond to.

**Structured / scannable** — bullet lists, markdown tables, time-stamped
section breakdowns, bolded element inventories. Models default to this style
when given a shorter, more administrative prompt. Useful when the user wants to
*analyze* or *catalog* the audio. Invoke with:

```bash
python scripts/describe_audio.py track.mp3 \
  --prompt "Describe this track in exquisite detail so it can be parsed by a text-only-modality LLM."
```

Same model, same audio, very different output character. The choice depends
on the user's intent — feel vs. analyze.

## What it does

1. Detects format. If non-mp3, transcodes to mp3 at 192kbps via ffmpeg.
2. Preflights openrouter.ai reachability; exits with a clear message if blocked.
3. Checks size. If file ≤ 5MB, sends as one chunk. Otherwise computes chunk
   duration based on actual bitrate, splits losslessly with `ffmpeg -c copy`.
4. Discards any tail-artifact chunks below 50KB (ffmpeg's keyframe-aligned
   segmenter occasionally emits a tiny zero-duration tail).
5. For each remaining chunk: base64-encodes, POSTs to OpenRouter with the
   description prompt, captures the response, checks `audio_tokens`.
6. Prints structured markdown to stdout: title, duration, then one section
   per chunk with timestamps and description.

## Metadata & embedded lyrics

MP3s can carry title/artist/lyrics in ID3 tags. Read them BEFORE the API call —
free ground truth beats paid transcription:

```bash
pip install mutagen --break-system-packages -q
python3 -c "
from mutagen.id3 import ID3
tags = ID3('track.mp3')
for key in tags.keys(): print(key, ':', str(tags[key])[:300])
"
```

Look for `TIT2` (title), `TPE1` (artist), `USLT` (unsynced lyrics),
`SYLT` (synced lyrics).

**Caveat:** files pulled straight off a CDN are often nearly bare — a comment
frame and an encoder string, no title, no lyrics. Tagged versions usually come
from the service's own Download button. Check tags anyway — it takes one second
and the file source varies.

## Onomatopoeia warning

(See also "The silent-drop trap" above — the same confabulation tendency operating one
level up, on the whole track rather than a syllable.)

Audio models confidently mis-transcribe non-word vocals into real words: a
repeated "meow" came back as "mail" from one model and "kneel" from another, on
the same file. The default prompt instructs literal transcription of
sounds-as-sounds, but **treat any transcribed lyric as a hypothesis, not ground
truth.** When lyrics matter, prefer embedded ID3 lyrics or ask the user.

Note the confound if you go testing this: naming an example sound in the prompt
primes it. A clean test swaps the examples.

## Reading the output

Output is markdown to stdout. Multi-chunk pieces have multiple sections
labeled with timestamps (e.g. `chunk 2/3, 4:30–9:00`). Claude can synthesize
across chunks if a unified picture is wanted — chunk descriptions are
independent and don't assume cross-chunk context.

Tempo estimates are not reliable. Three listens of the same track returned
110 / 120 / 140–145 BPM. Report a BPM figure as the model's guess, or not at all.

## Cost

Sep 2026 rates. Gemini 3.8 / 3.7 Flash: $0.75/M input (audio at the same rate),
$3.75/M output; the `:batch` variant is half that if latency is irrelevant. A
3:37 track at 64kbps mono tokenized to ~4.3k audio tokens — well under a cent.
`--cross-check` roughly doubles it. `--shrink` cuts input tokens as well as wall
clock. There is no cheap tier worth having: the $0.14/M option was not listening.

A $1/month spend cap is plenty for personal use. Set one — see
`references/api-key-setup.md`.

## Failure modes worth knowing

- **No key found**: script exits with the resolution order and where to write it
- **openrouter.ai unreachable**: preflight catches this and exits with a
  message explaining the runtime requirements (see above)
- **HTTP errors from OpenRouter**: reported inline per chunk; other chunks proceed
- **ffmpeg not installed**: script exits with install hint
- **Unsupported format**: script lists supported extensions
- **Very long files**: each chunk is an independent API call; cost scales
  linearly with duration
- **Silent death in a sandbox**: empty stdout *and* empty stderr almost always
  means the process was reaped between tool calls. Re-run in the foreground
  with `--shrink`; do not debug the script
- **Content starting mid-sentence**: `--max-tokens` too low for a thinking model
- **Empty description with no HTTP error**: the model did not receive audio.
  Check `audio_tokens` in the warning line. Switch models
- **A confident description that contradicts what the user said the track is**:
  same cause, worse presentation — the model invented it from the prompt. Check
  `audio_tokens` before believing any of it
- **Output is all lyrics, no timbre**: you picked a speech model, not a music model
