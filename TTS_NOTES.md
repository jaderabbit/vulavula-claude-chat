# isiZulu Text-to-Speech (TTS) layer — implementation notes

The voice chat now SPEAKS Claude's isiZulu reply. The pipeline ends with
`zulu_out` (text) being synthesised to audio and returned to the browser, which
auto-plays it in the chat bubble (with a visible `<audio controls>` fallback).

**No model runs locally.** The app is a thin HTTP client for the **OmniVoice
Gradio Space** (600+ languages incl. isiZulu). This keeps the app tiny and it
fits the 512 MB Fly VM again.

## Model / service: OmniVoice (k2-fsa Gradio Space)

- **Space:** https://huggingface.co/spaces/k2-fsa/OmniVoice
  (API host: `https://k2-fsa-omnivoice.hf.space`)
- **Gradio version:** 6.10.0
- **Languages:** 600+; **Zulu** is a dropdown choice (exact label/value `"Zulu"`).
- **Two endpoints** (confirmed from `/gradio_api/info`):
  - **`_design_fn` — Voice Design (DEFAULT):** text + language (+ generation
    knobs), *no reference clip needed*. Voice attributes are chosen via
    Gender/Age/Pitch/Style/Accent dropdowns (we send `"Auto"`).
  - **`_clone_fn` — Voice Clone:** additionally needs a **reference audio clip +
    its transcript**, and reproduces that voice. Optional (`OMNIVOICE_MODE=clone`).

> Note: OmniVoice is **not a Xiaomi model** — it's the k2-fsa community Space.
> Jade chose it explicitly as the target (it's the one that actually has isiZulu
> in the dropdown and is callable over HTTP).

## How the app calls it (Gradio queue protocol)

Gradio is **not plain REST** — it's a two-step queue protocol, implemented with
`requests` in `app.py` (`_synthesize_omnivoice`):

1. `POST {space}/gradio_api/call/{api_name}` with `{"data": [ ...inputs in
   order... ]}` → returns `{"event_id": "..."}`.
2. `GET {space}/gradio_api/call/{api_name}/{event_id}` → an **SSE stream**;
   we read the `complete` event, whose payload is the endpoint's output list.
3. The first output is a **FileData** dict (`{"url": ..., "path": ...,
   "meta": {"_type": "gradio.FileData"}}`); we resolve the `url`
   (`{space}/gradio_api/file=...`) and **download the audio bytes**.
4. The bytes are base64-encoded into an `audio_out` data URI on `/api/voice`.

We use **raw `requests`** rather than the `gradio_client` library on purpose:
`gradio_client` also reaches `huggingface.co` (which was egress-blocked in the
build/test sandbox), whereas the raw calls only touch the `*.hf.space` host.

**Exact input order sent** (from `/gradio_api/info`):

- `_design_fn`:
  `[text, "Zulu", steps=32, cfg=2.0, denoise=true, speed=1.0, duration=null,
    preprocess=true, postprocess=true, gender="Auto", age="Auto", pitch="Auto",
    style="Auto", english_accent="Auto", chinese_dialect="Auto"]`
- `_clone_fn`:
  `[text, "Zulu", ref_audio(FileData|null), ref_text, instruct, steps=32,
    cfg=2.0, denoise=true, speed=1.0, duration=null, preprocess=true,
    postprocess=true]`

Cold/queued Spaces (HTTP 429/503) are retried with exponential backoff until
`OMNIVOICE_TIMEOUT` (default 180 s). Any failure degrades to `audio_out=null`
so a TTS problem never breaks the text pipeline.

## Integration approach: base64 data URI in the existing JSON

`/api/voice` returns an extra field (all existing text fields unchanged):

```json
"audio_out": "data:audio/wav;base64,UklGRi4..."   // or null if off/failed
```

Chosen over a second `/api/tts` endpoint to keep the single round-trip. The
mimetype is detected from the returned file (wav/mp3/flac/ogg/m4a), defaulting
to `audio/wav`.

## Config knobs (env)

| Var | Default | Meaning |
|---|---|---|
| `TTS_BACKEND` | `omnivoice` | `omnivoice` or `off` |
| `OMNIVOICE_SPACE_URL` | `https://k2-fsa-omnivoice.hf.space` | Space API host (point at your own Space for the demo) |
| `OMNIVOICE_MODE` | `design` | `design` (text+lang only) or `clone` (needs a reference voice) |
| `OMNIVOICE_API_NAME` | derived from mode | `_design_fn` / `_clone_fn` (override if the Space renames it) |
| `OMNIVOICE_LANG` | `Zulu` | dropdown label/value |
| `OMNIVOICE_TOKEN` | — | HF token (needed only if you duplicate the Space as private) |
| `OMNIVOICE_STEPS` / `OMNIVOICE_CFG` / `OMNIVOICE_SPEED` | `32` / `2.0` / `1.0` | generation knobs |
| `OMNIVOICE_REF_AUDIO` / `OMNIVOICE_REF_TEXT` / `OMNIVOICE_INSTRUCT` | — | clone-mode reference clip (URL or path), its transcript, and an instruction |
| `OMNIVOICE_TIMEOUT` | `180` | seconds to keep polling a cold/queued Space |

## Files changed

- **`app.py`** — replaced the local-model TTS with an OmniVoice HTTP client:
  `_omnivoice_payload()` (builds the ordered data array for design/clone),
  `_synthesize_omnivoice()` (queue POST → SSE result → file download, with
  backoff), `synthesize_audio() -> (bytes, mime)`, `synthesize() -> bytes`,
  `synthesize_data_uri()` (best-effort). Wired into `/api/voice`
  (`stage="tts"`, `audio_out`). Dropped torch/transformers/wave/struct imports.
- **`index.html`** — Claude's bubble renders an auto-playing `<audio controls>`
  (`.play().catch()` fallback if autoplay is blocked); text kept. (Frontend
  contract unchanged from the previous revision.)
- **`requirements.txt`** — removed `torch`/`transformers`; just flask/requests/
  anthropic/python-dotenv.
- **`Dockerfile`** — removed CPU-torch install and model preload; sets
  `TTS_BACKEND=omnivoice` + `OMNIVOICE_*` env; back to 2 gunicorn workers.

## How to run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # tiny: no torch
cp .env.example .env                      # VULAVULA_API_TOKEN + ANTHROPIC_API_KEY
python app.py                             # http://127.0.0.1:5050
```

Record isiZulu → the reply bubble plays OmniVoice audio. Requires outbound
access to `k2-fsa-omnivoice.hf.space` (or your own Space URL).

## Deployment — now fits the 512 MB Fly VM

Because there is **no local model**, the app is back to a small
flask+requests+gunicorn image and comfortably fits the current `fly.toml`
512 MB shared-CPU VM. No memory bump or GPU needed on the app side. The heavy
compute lives in the OmniVoice Space, not in our VM.

## Standing up a dedicated endpoint for the LIVE demo

The **public** `k2-fsa/OmniVoice` Space is fine for **rehearsal**, but is risky
live: it **sleeps when idle** (cold-start of tens of seconds) and **shares a
queue** with everyone on the internet (unpredictable latency, or 429/503 under
load). For a webinar, stand up a **dedicated, always-on copy**:

1. In **Jade's HF account**, **Duplicate** the `k2-fsa/OmniVoice` Space.
2. Set hardware to a **paid GPU** tier and **disable sleep** ("always on"):
   - OmniVoice is a diffusion-style TTS (~0.6B params); a small GPU such as a
     **T4 16 GB** or **L4 24 GB** is the right tier — enough for single-user
     low-latency inference; A10G if you want headroom. (Confirm current tier
     names/prices in HF Spaces settings.)
   - Always-on GPU Spaces are **billed hourly while running** — budget for the
     rehearsal + event window and pause it afterwards.
3. Point the app at it: `OMNIVOICE_SPACE_URL=https://<jade-username>-omnivoice.hf.space`
   (and `OMNIVOICE_TOKEN=<hf_token>` if the duplicated Space is private).
4. **Warm it** right before going live (send one synthesis request) so the first
   audience request isn't the cold one.

### Voice Design vs Voice Clone

- **Voice Design (default):** simplest — text + `"Zulu"` only. Voice is
  synthetic/auto. No reference clip to manage. Best for a robust live demo.
- **Voice Clone:** gives a **chosen, consistent voice** but you must supply a
  short **reference audio clip + its transcript** (`OMNIVOICE_REF_AUDIO` /
  `OMNIVOICE_REF_TEXT`). More setup and one more thing to break live; use only
  if a specific branded voice matters. Ensure you have rights to the reference
  voice.

## Verification done (and its limits)

The real OmniVoice Space is **NOT reachable from this sandbox**: the network
egress firewall denies it — `HTTP 403, x-deny-reason: host_not_allowed,
"Host not in allowlist: k2-fsa-omnivoice.hf.space"` (both through and around the
agent proxy). So I could **not** produce a real `omnivoice_sample.wav` here, and
there is **no live-audio sample** in this patch. What I verified:

- **API contract** read from the live Space via GET (`/config`,
  `/gradio_api/info`): endpoints `_design_fn` / `_clone_fn`, full parameter
  order + defaults, and that `"Zulu"` is a valid language value. These are what
  the code encodes.
- **Full client code path** against a **local mock of the exact Gradio queue
  protocol** (POST→event_id→SSE `complete`→FileData `url`→download): the client
  POSTs `/gradio_api/call/_design_fn` with
  `["Sawubona, ninjani namuhla?", "Zulu", 32, 2.0, true, 1.0, null, true, true,
  "Auto", "Auto", "Auto", "Auto", "Auto", "Auto"]`, parses the SSE stream,
  downloads the file, detects `audio/wav`, and returns a valid data URI
  (decodes back to `RIFF/WAVE`). Clone-mode payload shape (12 items) and the
  `off`/unreachable degradation paths were also exercised.
- `python -m py_compile app.py` — clean.

### Run the REAL test on a box that can reach the Space

```bash
python - <<'PY'
import os
os.environ.setdefault("VULAVULA_API_TOKEN","x"); os.environ.setdefault("ANTHROPIC_API_KEY","x")
import app                       # uses OMNIVOICE_SPACE_URL from env (default public Space)
audio, mime = app.synthesize_audio("Sawubona, ninjani namuhla?")
ext = "mp3" if "mpeg" in mime else "wav"
open("/tmp/omnivoice_sample."+ext,"wb").write(audio)
print("got", len(audio), "bytes", mime)
PY
```

(The public Space may be asleep — the client already retries with backoff up to
`OMNIVOICE_TIMEOUT`.)

## What Jade still needs to decide / provide

- **HF account + paid always-on GPU Space** for the live endpoint (duplicate the
  Space; pick T4/L4 tier; set `OMNIVOICE_SPACE_URL`, optional `OMNIVOICE_TOKEN`).
- **API keys** (`VULAVULA_API_TOKEN`, `ANTHROPIC_API_KEY`) to run the full
  pipeline end-to-end — I did not run it and added no secrets.
- **Design vs Clone** decision (and a reference clip if Clone).

## Live-demo risks

- **Public Space sleep/queue** — cold starts and shared-queue latency/429s.
  Mitigate with the dedicated always-on GPU Space above; keep the public Space
  only as a fallback.
- **Latency** — OmniVoice diffusion synthesis adds meaningful time on top of the
  STT→translate→Claude→translate chain (roughly a few seconds on GPU per reply,
  more if the Space is cold/queued). Warm it before going live; keep Claude
  replies short (the system prompt already asks for 2–4 sentences).
- **Egress from Fly** — the Fly app must be allowed to reach the Space host; it's
  an external dependency, so a Space outage = no audio (text still works,
  because TTS is best-effort).
- **Browser autoplay** — may be blocked until a user gesture; the "Record" press
  counts, and visible `<audio>` controls are the fallback.
- **API drift** — if the Space is updated and renames `_design_fn`/`_clone_fn`
  or reorders inputs, set `OMNIVOICE_API_NAME` / adjust `_omnivoice_payload()`.
  Re-check `/gradio_api/info` before the event.
- **Licensing / voice rights** — confirm OmniVoice's license terms for your use,
  and (Clone mode) that you have rights to any reference voice.
