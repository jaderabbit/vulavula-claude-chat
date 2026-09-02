# vulavula-claude-chat

A voice chat with Claude in **isiZulu**, powered by [Lelapa AI's Vulavula](https://lelapa.ai) for transcription and translation, and [OmniVoice](https://huggingface.co/spaces/k2-fsa/OmniVoice) for speech.

Speak isiZulu in the browser → Vulavula transcribes → Claude restores punctuation/casing → Vulavula translates to English → Claude replies → Vulavula translates back to isiZulu → OmniVoice speaks it. Multi-turn, chat-style UI.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in VULAVULA_API_TOKEN and ANTHROPIC_API_KEY
python app.py
```

Requires `ffmpeg` on the PATH (browser records webm/opus; Vulavula wants wav).

Open <http://127.0.0.1:5050>.

## Settings

Only the two tokens are required; everything else has a working default.

| Variable | Default | Notes |
| --- | --- | --- |
| `VULAVULA_API_TOKEN` | — | **Required.** Vulavula transcribe + translate. |
| `ANTHROPIC_API_KEY` | — | **Required.** Needs a funded account, or the pipeline fails at `stage: "claude"`. |
| `PORT` | `5050` | |
| `PUNCTUATE_TRANSCRIPT` | `1` | Restore punctuation + casing on the raw transcript before translating. `0` skips it and saves a round trip. |
| `PUNCTUATE_MODEL` | `claude-haiku-4-5` | Model for that pass. Haiku is ~2s faster per turn than `claude-opus-5`, with near-identical results. |
| `TTS_BACKEND` | `omnivoice` | `off` disables speech; the pipeline still returns text. |
| `OMNIVOICE_SPACE_URL` | `https://k2-fsa-omnivoice.hf.space` | The public Space cold-starts and queues (first call can take ~a minute). Point this at your own duplicated always-on Space for a live demo. |
| `OMNIVOICE_MODE` | `design` | `design` = text + language only. `clone` reproduces a reference voice and needs `OMNIVOICE_REF_AUDIO` + `OMNIVOICE_REF_TEXT`. |
| `OMNIVOICE_API_NAME` | from mode | `_design_fn` or `_clone_fn`. |
| `OMNIVOICE_LANG` | `Zulu` | Must match the Space's dropdown label exactly. |
| `OMNIVOICE_TOKEN` | empty | **Recommended.** The public Space is ZeroGPU; anonymous callers share a small per-IP quota and TTS silently degrades to text-only once it runs out. A token gives you your own quota (PRO: 8x + queue priority). Also required for a private duplicated Space. |
| `OMNIVOICE_TIMEOUT` | `180` | Seconds to wait out a cold or queued Space. |
| `OMNIVOICE_STEPS` / `_CFG` / `_SPEED` | `32` / `2.0` / `1.0` | Generation knobs. |
| `OMNIVOICE_REF_AUDIO` / `_REF_TEXT` / `_INSTRUCT` | empty | Voice-clone mode only. |

TTS failures degrade gracefully: `/api/voice` returns `audio_out: null`, the bubble shows an "audio unavailable" note, and the reply still appears as text. If that note keeps appearing, check the server log — an exhausted ZeroGPU quota is the usual cause, and `OMNIVOICE_TOKEN` is the fix. See `TTS_NOTES.md` for how the Gradio queue protocol is called.

## Deploy to Fly.io

```bash
brew install flyctl
fly auth login
fly launch --no-deploy --copy-config --name vulavula-claude-chat
fly secrets set VULAVULA_API_TOKEN=... ANTHROPIC_API_KEY=...
fly deploy
```

The Dockerfile installs `ffmpeg` and serves the app with gunicorn on port 8080.
`fly.toml` sets `min_machines_running = 1` so one machine stays warm and demos don't pay a cold start. Set it back to `0` (with `auto_stop_machines = "stop"`) to scale to zero when idle.
No model runs on the VM — TTS is an outbound HTTP call — so it still fits a 512 MB machine.

## Pieces

- `POST https://api.lelapa.ai/v1/transcribe/sync` with `lang_code=zul`, header `X-CLIENT-TOKEN`
- `POST https://api.lelapa.ai/v1/translate/process` with `zul_Latn` ↔ `eng_Latn`
- Anthropic SDK, model `claude-opus-4-7`
- OmniVoice Gradio Space, `_design_fn` with language `Zulu`
