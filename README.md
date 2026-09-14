# audio-listening

```
in   ▁▂▃▅▇█▇▅▃▂▁▂▄▆█▆▄▂▁▁▂▃▅▇█▇▅▃▂▁
     ──────────► openrouter ──────────►
out  "warm sawtooth growl under a dry, close-mic'd snare;
      the key never resolves — it just stops"
                                    ↳ audio_tokens: 4316 ✓
```

A [Claude Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
that gives Claude a **pseudo-ability to hear music**. Claude can't perceive
audio. An audio-capable model on OpenRouter can. This skill is the translation
layer between them: audio in, exquisite prose out, for a text-only model to
read in place of listening.

## What it does

**Listens.** Point it at an mp3/wav/flac/m4a/ogg/opus and it transcodes,
chunks, sends, and returns timbre, instrumentation, structure, emotional arc,
production character — the things a lyric transcript throws away.

**Refuses to fake it.** The entire design problem here is that the failure mode
is *invisible*. A model that never received the audio still returns fluent,
confident music criticism. The skill checks
`usage.prompt_tokens_details.audio_tokens` on every single call and stamps a
loud warning into the output when it comes back zero.

**Fits in a sandbox.** `--shrink`, `--max-seconds` and incremental `--out`
exist because claude.ai's code sandbox reaps background processes between tool
calls and gives you about two minutes of foreground wall clock per call.

## The trap this skill exists to document

An audio request can succeed without the audio ever reaching the model: HTTP
200, the `input_audio` block accepted, a bill for plain text, no error.

| request | prompt_tokens | audio_tokens | result |
|---|---|---|---|
| MiMo, logged as `xiaomi/mimo-v2.5` | 1130 | **0** | 1499 reasoning tokens, empty content |
| `google/gemini-3.8-flash` | 4340 | **4316** | full description |

An earlier version of this README blamed the model and said MiMo v2.5 can't
hear. It can. Two likelier causes, neither confirmed:

1. **The wrong slug.** `xiaomi/mimo-v2.5-pro` is text-only, and models asked to
   use MiMo keep reaching for `-pro` as the "better" one, however plainly
   they're told not to.
2. **Provider routing.** OpenRouter serves `xiaomi/mimo-v2.5` from several
   providers and picks one per request. One that doesn't pass audio through
   gives exactly this result.

The empty response is the *harmless* case — it fails loudly. The dangerous case
is the non-empty one: given a shorter prompt, a MiMo request with the same
problem described a sea shanty as drum-and-bass with rubbery wobble bass and a
Lapfox-adjacent lineage, complete with a fabricated phonetic transcription of
an intro the model had never received. Nothing in the output marks it as
invention. Claude reads a description as perception; there is no seam to
notice.

Neither `input_modalities` nor pricing catches it — `xiaomi/mimo-v2.5` hears
audio but lists no separate audio rate. One check does: **`audio_tokens > 0` in
the response.** It means the audio was tokenized, not merely accepted, and it
catches a wrong slug and a bad route alike. The script checks it on every call.

A second, free and instant: if the output is all lyrics and nothing about
timbre, you picked a speech model. Most models advertising audio input are ASR.
They hear words, not music.

## Structure

```
audio-listening/
├── SKILL.md                     # triggering, runtime matrix, failure modes
├── scripts/
│   └── describe_audio.py        # transcode, chunk, POST, verify audio_tokens
├── references/
│   └── api-key-setup.md         # the 3-minute setup walkthrough Claude runs with you
└── config.example.json          # copy to config.json, gitignored
```

## Install

**Claude.ai** — Settings → Capabilities → Skills → upload the packaged
`.skill` file. The sandbox route additionally needs `openrouter.ai` on your
domain allowlist (Settings → Capabilities), **which is not reachable from the
mobile app** — do that part on web or desktop, and start a fresh chat
afterwards, because allowlist rows are baked in at container start.

**Claude Code** — drop the folder into `~/.claude/skills/audio-listening/`.
No allowlist, no wall clock, nothing else to do.

## Setup

You supply your own OpenRouter key; none ships with this. Ask Claude to walk
you through [`references/api-key-setup.md`](references/api-key-setup.md), or:

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

```bash
python scripts/describe_audio.py track.mp3 --shrink --out ears.md
python scripts/describe_audio.py track.mp3 --cross-check
python scripts/describe_audio.py --list-live-models
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
