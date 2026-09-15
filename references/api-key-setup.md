# Getting an OpenRouter key for this skill

**Claude: this is a walkthrough to run *with* the user, not a page to summarize
at them.** Go one step at a time, wait for them to come back, and check the
result before moving on. Step 2 is the one that makes the rest of this safe —
do not skip past it because the user sounds like they're in a hurry.

Budget about three minutes on desktop. On mobile, see the section at the bottom
first — one step cannot be done from the app at all.

---

## 1. Create the key

1. Sign in at <https://openrouter.ai> and open **Keys**
   (<https://openrouter.ai/settings/keys>).
2. **Create Key.** Name it something you'll recognise later — `claude's ears`
   is the name the original used.
3. Set a **credit limit** on the key. This is the field that matters. **$1 is
   plenty** — a three-minute track costs well under a cent, so a dollar buys
   hundreds of listens.
4. Copy the key. It is shown **once**. It looks like `sk-or-v1-…`.

You'll also need credits on the account itself for the key to spend; OpenRouter
requires a balance before paid models will run.

## 2. Why the cap is the whole security story

This skill stores a key on disk in a file an LLM is about to read, on a machine
you may not fully control. That is fine **if and only if** you have already
decided how much the key is allowed to cost you.

A spend-capped key is not a secret being mishandled. It's a token whose owner
priced the leak in advance. If it ever escapes, the blast radius is one dollar
and one click on **Revoke**.

What this does *not* cover: an uncapped key, a key that's also used for
something else, or a key belonging to an employer. Don't use any of those here.
Make a fresh one for this skill.

## 3. Put the key where a non-interactive shell can find it

`export OPENROUTER_API_KEY=...` in a terminal works exactly once — in that
terminal. Agent runtimes spawn a **fresh shell per tool call**, so the export is
gone by the next command, and the failure looks like a broken script rather
than a missing variable.

Write a file instead. From the skill directory:

```bash
echo '{"openrouter_api_key": "sk-or-v1-PASTE_HERE"}' > config.json
```

Or, if you'd rather keep it outside the skill folder:

```bash
mkdir -p ~/.config/audio-listening
echo '{"openrouter_api_key": "sk-or-v1-PASTE_HERE"}' > ~/.config/audio-listening/config.json
chmod 600 ~/.config/audio-listening/config.json
```

Then confirm:

```bash
python scripts/describe_audio.py --check-key
```

It prints where the key was found and a masked preview. `config.json` and
`.env` are in `.gitignore` — keep them that way.

## 4. claude.ai only: the domain allowlist

Skip this if you're running in Claude Code or through a local-shell connector —
those have normal network access.

The claude.ai code sandbox blocks outbound traffic except to an allowlist you
control: **Settings → Capabilities → Domain allowlist → Additional allowed
domains.** Add:

```
openrouter.ai
```

Plus one row for **every host the audio itself comes from**, if Claude will be
downloading it rather than you uploading it.

Four things that cost people time here:

- **Rows are baked in at container start.** Adding a row mid-conversation does
  nothing for that conversation. **Start a fresh chat** afterwards.
- **Wildcards don't work.** `*.example.com` is accepted by the field and then
  doesn't match anything, despite the UI placeholder suggesting otherwise. Use
  flat hostnames.
- **One host per row.** A comma-separated paste is stored as one long invalid
  entry.
- Share links usually redirect to a *different* CDN hostname than the one you
  pasted. Resolve the redirect first, then allowlist the host you actually land
  on.

## 5. Test on a real track

Not a beep, not a generated sine — a real piece of music you already know.

```bash
python scripts/describe_audio.py ~/Music/something_you_know.mp3 --shrink
```

Read the result against the track:

- **Does it describe timbre, arrangement and structure?** Good, that's a music
  model.
- **Is it mostly a lyric transcript with nothing about the instruments?** You
  picked a speech model. Most models advertising audio input are ASR. Switch.
- **Does stderr report `N audio tokens ingested`?** That proves the audio
  arrived. A zero proves nothing: some providers report 0 even when the model
  heard every note. Check the result against something the prompt couldn't
  supply — lyrics you know, or where the drop lands.
- **Treat every transcribed lyric as a guess**, including confident ones.
  Non-words get snapped to the nearest real word — a repeated "meow" has come
  back as "mail" and as "kneel" from different models on the same file.

## 6. Mobile

The app can *run* the skill fine. Setting it up from a phone is where it bites:

- **The domain allowlist page is not in the mobile app.** Not hidden, not
  buried — not there. If you need the claude.ai sandbox to reach
  `openrouter.ai`, that step has to happen on web or the desktop app. Everything
  else can be done from the phone.
- The OpenRouter dashboard works in a mobile browser, but the key is displayed
  once and copying it between browser and app is the fiddly part. Paste it
  somewhere you can retrieve it before navigating away.
- If you're setting up on a phone anyway: do the key on mobile, note that
  allowlist edits need a desktop pass *and* a fresh chat afterwards, and expect
  the sandbox route to stay blocked until both have happened.

The graceful failure is naming the wall early. If Claude hits
`403 host_not_allowed` or finds no key, the right output is one sentence saying
so — not twenty turns of creative routing around a setting only you can change.
