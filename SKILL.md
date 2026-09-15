---
name: audio-listening
description: "Use when user shares audio (mp3/wav/m4a/flac/ogg/opus) and Claude needs to perceive its sonic contents — music, mood, instrumentation, lyrics, loudness, structure. Two channels: describe_audio.py sends the audio to an audio-capable model on OpenRouter for a written description; look_at_audio.py renders spectrograms, waveforms and EBU R128 loudness locally, with no key and no network, for Claude to view directly. Use both when the claims matter."
dependencies: python>=3.8
---

# audio-listening

Claude can't perceive audio natively. This skill gives it two ways at the same
file, and they are not redundant.

| | `describe_audio.py` — the relay | `look_at_audio.py` — the picture |
|---|---|---|
| what it does | sends audio to an audio-capable model, returns prose | renders PNGs and measures loudness locally |
| what Claude gets | another model's **report** of the sound, in words | the **signal**, in a modality Claude has: vision |
| needs | API key, network, well under a cent per track | ffmpeg only. No key, no network, free |
| good at | words, mood, genre, instruments, who's singing | level, spectrum, structure, silence, timing |
| fails by | inventing content that isn't there | having no content at all |

**Use both when a claim matters.** They fail in opposite directions, so
agreement between them is worth more than either alone. On anything physical
— level, spectral content, where the silences are — the picture wins. On
anything semantic — what the words are, whether it's menacing or euphoric —
the relay wins, because the picture cannot know.

**There is a third failure, and it's the reader's.** Worked example (Sep
2026): the relay said a bridge was where "the rhythm falls away entirely into a
wide ambient pool." Claude took that to mean the bridge gets quieter. The
waveform showed the bridge at the same level as the drop next to it, and EBU
R128 put the whole track at 3.4 LU of loudness range. The relay was right — the
drums did stop. The level drop was Claude's own inference, filled in from how
music usually behaves, and it felt like part of the report. When you pass a
description on, keep what the relay said apart from what you concluded from
it, and check physical conclusions on the picture.

# Part 1 — the relay (`describe_audio.py`)

A translation layer between audio and text. An audio-capable model on
OpenRouter listens; this script sends it the audio with a description prompt
and returns the description for Claude to read.

## Setup — do this first if there is no key yet

The relay needs an OpenRouter API key. The skill ships without one. The
picture (Part 2) needs no key at all.

**Claude: if `--check-key` reports no key, stop and walk the user through
[`references/api-key-setup.md`](references/api-key-setup.md) before running
the relay.** If the user wants something in the meantime, offer the picture.
Setup takes about three minutes, covers the monthly spend cap
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
  about a tenth of Gemini's cost per clip. Gemini 3.8 Flash still describes
  music better. Its providers report `audio_tokens: 0` even when it hears, and
  some runs come back empty — see below.
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
  timbre and arrangement detail, not just the lyrics. Run the checks below.

## The silent-drop trap — read before switching models

A request can carry an `input_audio` block, return HTTP 200, and never deliver
the audio to the model — and the model may still answer. A fluent description
invented from the prompt reads exactly like a real one.

One request described a sea shanty as drum-and-bass with rubbery wobble bass
and a Lapfox-adjacent lineage, complete with a phonetic transcription of the
intro. Whether that audio arrived is no longer known; either way, nothing in
the output marked it. **This is the worst possible failure for a translation
layer.** Claude reads the description as perception. There is no seam to
notice.

### The token counts don't catch it on every model

The obvious check is `usage.prompt_tokens_details.audio_tokens`. On Gemini it
works: a 40-second clip reported 1000 audio tokens. On `xiaomi/mimo-v2.5` it
means nothing. Measured Sep 2026, the same 40-second clip with known lyrics,
pinned to one OpenRouter provider at a time, three calls each:

| MiMo via | heard it | audio_tokens reported | prompt_tokens reported | misses |
|---|---|---|---|---|
| Xiaomi | 3 of 3 | 0 | 513 | — |
| GMICloud | 2 of 3 | 0 | 513 | one empty response |
| StreamLake | 2 of 3 | 0 (251 on an earlier run) | 20 | one `finish_reason: error` |
| DeepInfra | 1 of 3 | 0 | 291 | two empty: all 1500 tokens spent reasoning |

Every provider that answered had heard the clip, and every one reported zero
audio tokens while doing so. `prompt_tokens` is no better: 20 tokens can't
hold 40 seconds of audio, and StreamLake heard it anyway. On MiMo, neither
number tells you whether the audio arrived. The misses were all loud — empty
responses — and the DeepInfra ones are a budget problem, not deafness: the
model reasons until `--max-tokens` runs out. Rerun with a 3000 budget, DeepInfra
went 3 of 3, one run using 1654 reasoning tokens.

### The checks that do catch it

1. **`audio_tokens > 0`** — conclusive when present. Zero proves nothing.
2. **Content the prompt can't supply.** Lyrics that match the ID3 tags or what
   the user said; section timings and dynamics that match the waveform from
   `look_at_audio.py`. This is the check that settles it, on every model.
3. **Not `input_modalities`, not pricing.** `xiaomi/mimo-v2.5` hears audio and
   lists no separate audio rate.

The script prints the serving provider and both token counts for every chunk,
and marks a description unverified when audio tokens come back zero. Before
adopting any model, run a track with known lyrics through it and read the
transcript. Cheap is worthless if it isn't listening.

An earlier version of this skill called MiMo v2.5 "not an audio model", from
one request that returned `audio_tokens: 0`, 1499 reasoning tokens and an empty
answer. That matches the DeepInfra misses above: a spent budget, not a model
that can't hear. Models asked to use MiMo also keep swapping in
`xiaomi/mimo-v2.5-pro`, which really is text-only. Claude: if you are about to
type `-pro`, stop.

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
- No internet or no key — the relay cannot run. The picture still can

## Prerequisites

- `ffmpeg` and `ffprobe` on PATH
- An OpenRouter API key reachable by the script (see Setup) — relay only
- Internet access from the execution environment — relay only

## Runtime requirements (read this before invoking)

The relay needs two things at the same time, in the same execution environment:
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

2. **Network reachability.** If running in the claude.ai sandbox without
   `openrouter.ai` on the allowlist, the relay cannot complete — warn the
   user up front rather than letting the preflight fail mid-task. They'll
   need to allowlist it, or use Claude Code or a claude.ai connector that
   gives Claude local-machine shell access. The picture works regardless.

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

# Skip a provider that keeps failing (name as printed after "via")
python scripts/describe_audio.py track.mp3 --model xiaomi/mimo-v2.5 --ignore-providers DeepInfra

# What actually accepts audio on OpenRouter today, split by whether an audio rate is listed
python scripts/describe_audio.py --list-live-models

# Where is my key coming from?
python scripts/describe_audio.py --check-key
```

**`--max-tokens` covers reasoning too.** Thinking models spend budget on
reasoning before writing. Below ~800 the returned `content` is a fragment that
starts mid-sentence rather than a shorter description, and MiMo via DeepInfra
has spent a full 1500 on reasoning and returned nothing. Default is 3000 — you
pay only for tokens used. The script warns below 800.

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
clock. `xiaomi/mimo-v2.5` costs about a tenth as much per clip (measured: $0.0004
vs $0.0038 for 40 seconds) and does listen; Gemini describes music better.

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
- **Empty description with no HTTP error**: usually the model spent
  `--max-tokens` on reasoning — the output says so when that's the cause; raise
  it. Otherwise retry, or `--ignore-providers` the provider named in the output
- **A confident description that contradicts what the user said the track is**:
  the audio may not have arrived, or the model misheard. Run the checks in "The
  silent-drop trap" before believing any of it
- **Output is all lyrics, no timbre**: you picked a speech model, not a music model

---

# Part 2 — the picture (`look_at_audio.py`)

Renders audio as PNGs that Claude reads with its own vision, and measures EBU
R128 loudness. **No API key. No network. No cost.** Runs anywhere ffmpeg does,
including sandboxes where `openrouter.ai` is blocked. When the relay is
unavailable this is not a degraded fallback. It is a different instrument, and
on physical questions a stricter one.

## Invoking

```bash
python scripts/look_at_audio.py track.mp3                 # spectrogram + waveform + metrics
python scripts/look_at_audio.py *.mp3 --metrics-only      # comparison table, no images
python scripts/look_at_audio.py track.mp3 --outdir looks/ # default is ./looks
python scripts/look_at_audio.py track.mp3 --zoom 60:90    # images of one window, in seconds
python scripts/look_at_audio.py track.mp3 --spectrum-mode separate  # L/R panels
```

Then **view the PNGs.** The table is a summary; the images are the evidence.
Do not report structural claims from the table alone.

**Zoom gotchas:** inside a `--zoom` window the x-axis restarts at 0, so
timestamps are relative to the window start. Add the offset yourself. And
`--zoom` only affects the images; the numbers always cover the whole file.

## Reading a waveform

The cheapest and most informative image. At a glance:

- **Periodic vertical teeth** in loud sections = individual kick drums with
  hard sidechain compression. Each tooth is one kick ducking everything else.
- **A sustained dip** = the level really drops: a breakdown or a bridge. No dip
  where the relay described a sparse section is *not* a contradiction. "The
  drums drop out" is a claim about arrangement, and arrangement can thin at a
  constant level. It only contradicts a claim about loudness.
- **A flat sausage end to end** = brick-wall limiting, or platform
  normalization, or both. The music may still be dynamic in *texture*; it is
  not dynamic in *level*, and those get conflated constantly.
- **Isolated tall spikes** above the body = uncompressed transients, usually
  a lone hit or a vocal shriek that escaped the limiter.

## Reading a spectrogram

- **A hard horizontal edge with black above it** = a lossy codec lowpass baked
  into the source. Suno exports stop around 16–18 kHz. Unmistakable once seen;
  the numeric heuristic below is far less reliable.
- **Horizontal striations** (visible harmonic stacks) = individual tones are
  resolvable, nothing is masking them. These appear exactly where the
  arrangement thins out, so locating them locates the sparse passages.
- **Full-height dark columns** = actual silence, or near it. Mutes, stops,
  edit points.
- **A canvas filled DC-to-ceiling for the entire duration** = maximalist wall
  production, no spectral headroom at any moment.
- `--spectrum-mode separate` draws L and R as two panels. Near-identical
  panels mean the passage is essentially mono; visible differences mean real
  stereo width.

## Reading the numbers

`I` is integrated loudness (LUFS), `LRA` is loudness range (LU), `TP` is true
peak (dBFS), `>19k` is energy above 19 kHz relative to the full mix (dB). A `?`
under `>19k` means the file's sample rate is below 40 kHz, so there is nothing
up there to measure.

**LRA is the interesting one.** It measures how much the level actually moves
over the piece:

| LRA | means |
|---|---|
| < 3 LU | brick — nothing plays alone anywhere |
| 3–5 LU | flat — texture changes, level doesn't |
| 5–7 LU | some contour — likely one sparse passage |
| > 7 LU | genuinely dynamic — something plays unaccompanied |

These bands were calibrated on 13 tracks by one artist from one generator
(Suno, Sep 2026). Treat them as a starting point on other material.

In that set, LRA did **not** follow duration: a 6:09 track sat at 3.8 LU while
a 3:06 track hit 6.6. What predicted high LRA was *one genuinely sparse or
acoustic passage* — a solo instrument, an unaccompanied voice. Don't assume
long means dynamic; check.

**Platform normalization is detectable.** Across the same set, integrated
loudness spanned only 2.2 LU (−12.1 to −14.3) across genres from solo violin
cabaret to gabber. That uniformity is the generator normalizing output, not a
mixing choice. On such a platform the author controls arrangement dynamics
*within* a track, not level differences *between* tracks. The script flags this
when given 3+ files that all sit within 3 LU.

## Two traps, both verified the hard way

**1. `-v error` silently kills the ebur128 summary.** The filter prints its
summary at info level. Quieting ffmpeg quiets the exact thing you asked for,
and you get empty fields with a **zero exit code** — which looks like a
parsing bug and is not. Use `-hide_banner -nostats` instead: no banner, no
per-frame spam, info-level filter output intact.

```bash
# WRONG — returns nothing, exits 0
ffmpeg -v error -i t.mp3 -filter_complex ebur128=peak=true -f null -
# RIGHT
ffmpeg -hide_banner -nostats -i t.mp3 -filter_complex ebur128=peak=true -f null -
```

**2. A single-pole highpass measures its own leakage.** `highpass=f=19000`
has a slope so gentle that a file with a hard wall at 18 kHz still reads
about −53 dB — which looks like real high-end content. Cascade four
two-pole stages for a steep enough skirt, and report the result *relative to
the full mix*, not absolute. Even then it is a soft heuristic with a fuzzy
boundary: identical-provenance files land anywhere from −52 to −64 dB
depending on how much cymbal and distortion leak through. **Confirm the wall
on the spectrogram**, where there is nothing to argue about.

## When to use which

- **Relay only** — the user wants to know what a track is *like*. Mood, genre,
  lyrics, vibe check. The picture cannot help.
- **Picture only** — no network, no key, or the question is production:
  is it clipping, is it over-compressed, where are the sections, is the
  source lossy, why don't these tracks match in level.
- **Both** — any claim that will be repeated back to the user as fact,
  anything about structure or dynamics, and anything where the relay's
  description sounds a little too good. The cross-check costs one ffmpeg call.

## What neither channel can do

The picture has no semantics. A spectrogram of a song contains no evidence
about what is being sung, whether it's funny, or whether it lands. The relay
has semantics, but they are *reported*, not perceived, and reports can be
invented. Neither is hearing. Say so when it matters, rather than smoothing
over the seam.
