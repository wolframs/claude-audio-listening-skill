# audio-listening

```
in   ▁▂▃▅▇█▇▅▃▂▁▂▄▆█▆▄▂▁▁▂▃▅▇█▇▅▃▂▁
     ──────────► openrouter ──────────►
out  "warm sawtooth growl under a dry, close-mic'd snare;
      the key never resolves — it just stops"
                                    ↳ audio_tokens: 4316 ✓
```

A [Claude Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
that gives Claude a **pseudo-ability to hear music**, two ways. Claude can't
perceive audio, but an audio-capable model on OpenRouter can: the skill relays
the audio there and brings back prose for Claude to read in place of
listening. Claude can see, though: the skill also renders the audio as a
spectrogram and a waveform and measures its loudness, locally, for Claude to
look at.

## What it does

**Listens.** Point it at an mp3/wav/flac/m4a/ogg/opus and it transcodes,
chunks, sends, and returns timbre, instrumentation, structure, emotional arc,
production character — the things a lyric transcript throws away.

**Looks.** `look_at_audio.py` renders a spectrogram and a waveform and
measures EBU R128 loudness, with no key and no network. The relay says what a
track is like; the picture shows where the sections are, whether the level
ever moves, and whether the source is lossy. They fail in opposite directions —
the relay can invent, the picture has nothing to invent — so Claude is told to
use both when a claim matters, and to keep what the relay said apart from what
it inferred from it.

**Refuses to fake it.** The entire design problem here is that the failure mode
is *invisible*. A model that never received the audio can still return fluent,
confident music criticism. The skill checks
`usage.prompt_tokens_details.audio_tokens` on every call, prints which provider
served it, and marks the description unverified when the count is zero — then
tells Claude to check it against lyrics or the picture before trusting it.

**Fits in a sandbox.** `--shrink`, `--max-seconds` and incremental `--out`
exist because claude.ai's code sandbox reaps background processes between tool
calls and gives you about two minutes of foreground wall clock per call.

## The trap this skill exists to document

An audio request can succeed without the audio reaching the model, and the
model can still answer. A fluent description invented from the prompt reads
exactly like a real one. Claude reads a description as perception; there is no
seam to notice.

The obvious check is the `audio_tokens` count OpenRouter returns. It works on
Gemini. On `xiaomi/mimo-v2.5` it's useless. Same 40-second clip with known
lyrics, pinned to each provider three times (Sep 2026):

| MiMo via | heard it | audio_tokens reported | prompt_tokens reported |
|---|---|---|---|
| Xiaomi | 3 of 3 | 0 | 513 |
| GMICloud | 2 of 3 | 0 | 513 |
| StreamLake | 2 of 3 | 0 | 20 |
| DeepInfra | 1 of 3 | 0 | 291 |

Every provider that answered had heard the clip, and every one reported zero
audio tokens. The misses were empty responses, mostly the model reasoning until
its 1500-token budget ran out; with 3000, DeepInfra went 3 of 3. An earlier
version of this README took one such zero-token, empty MiMo response as proof
the model can't hear. It can. (Models also keep swapping in
`xiaomi/mimo-v2.5-pro`, which really is text-only.)

So the skill treats `audio_tokens > 0` as proof and zero as nothing. The check
that settles it is content the prompt can't supply: known lyrics, or section
timings that match the waveform from the picture channel. `input_modalities`
and pricing prove nothing either way.

A second, free and instant: if the output is all lyrics and nothing about
timbre, you picked a speech model. Most models advertising audio input are ASR.
They hear words, not music.

## Structure

```
audio-listening/
├── SKILL.md                     # triggering, runtime matrix, failure modes
├── scripts/
│   ├── describe_audio.py        # the relay: transcode, chunk, POST, check audio_tokens
│   └── look_at_audio.py         # the picture: spectrogram, waveform, EBU R128 loudness
├── references/
│   └── api-key-setup.md         # the 3-minute setup walkthrough Claude runs with you
└── config.example.json          # copy to config.json, gitignored
```

## Install

**Claude.ai** — Settings → Capabilities → Skills → upload the packaged
`.skill` file. For the relay, the sandbox route also needs `openrouter.ai` on
your domain allowlist (Settings → Capabilities), **which is not reachable from
the mobile app** — do that part on web or desktop, and start a fresh chat
afterwards, because allowlist rows are baked in at container start.

**Claude Code** — drop the folder into `~/.claude/skills/audio-listening/`.
No allowlist, no wall clock, nothing else to do.

Both scripts need `ffmpeg` and `ffprobe` on PATH and nothing outside the Python
standard library.

## Setup

The relay needs an OpenRouter key; none ships with this. The picture needs
nothing. Ask Claude to walk you through
[`references/api-key-setup.md`](references/api-key-setup.md), or:

```bash
# 1. openrouter.ai/settings/keys → Create Key → set a credit limit of $1
# 2. write it where a non-interactive shell can find it:
echo '{"openrouter_api_key": "sk-or-v1-..."}' > config.json
# 3. confirm:
python scripts/describe_audio.py --check-key
```

**Use a config file, not `export`.** A shell export dies with that shell, and
every agent tool call spawns a fresh one — the symptom looks like a broken
script rather than a missing variable.

**The spend cap is the security control, not secrecy.** This skill puts a
credential on disk in a file an LLM is about to read. That's fine if you've
already priced the leak: a $1/month key that escapes costs you one dollar and
one click on Revoke. A three-minute track costs well under a cent, so a dollar
is hundreds of listens. Don't reuse a key that has any other job.

## Use

> "Listen to this and tell me if the bridge earns the key change"
>
> "Is this the right vibe for the trailer?"
>
> "What's actually in the low end here?"
>
> "Cross-check this one — I think the genre frame is wrong"
>
> "Does the bridge actually get quieter, or does it just thin out?"
>
> "Why do these twelve tracks all sit at the same loudness?"

```bash
python scripts/describe_audio.py track.mp3 --shrink --out ears.md
python scripts/describe_audio.py track.mp3 --cross-check
python scripts/describe_audio.py --list-live-models
python scripts/look_at_audio.py track.mp3                # PNGs into ./looks + metrics
python scripts/look_at_audio.py '*.mp3' --metrics-only   # loudness table across a set
```

## Honest limitations

Everything downstream of the API call is a **report**, not perception. Treat
transcribed lyrics as hypotheses — non-words get snapped to the nearest real
word, and the same repeated "meow" has come back as "mail" and as "kneel" from
different models on the same file. Tempo estimates are unreliable: three
listens of one track returned 110, 120, and 140–145 BPM. There may also be a
texture floor — three models independently called a track cold and bone-dry
whose brief asked for vinyl crackle and rain, which is either the generator
dropping the clause or both model families failing to hear grain. Untested.

The picture has the opposite limit: no semantics at all. A spectrogram holds no
evidence of what is being sung or whether it lands. Its loudness-range bands
were calibrated on 13 tracks by one artist from one generator, so treat them as
a starting point on anything else.

The model roster rots fast. `mimo-v2-omni` 404'd; the Gemini flash line ships
new checkpoints every few weeks. Don't trust the model list in `SKILL.md` —
run `--list-live-models`.

It also improves. Most models that take audio were trained on speech and hear
words, not music, but every now and then a model trained on music itself shows
up. `google/gemini-3.8-flash` is the best listener so far; `xiaomi/mimo-v2.5`
works and costs a fraction of that. When something new appears, run a track
you know through it next to the default and judge the timbre and arrangement
detail, not the lyrics.

## Provenance

Written on a whim, for one person, to answer a narrower question than it now
documents: what does my own generated music actually sound like to something
that isn't me? Then it got audited, and the audit turned out to be the
interesting part.
