# vulavula-claude-chat

A voice chat with Claude in **isiZulu**, powered by [Lelapa AI's Vulavula](https://lelapa.ai) for transcription and translation.

Speak isiZulu in the browser → Vulavula transcribes → Vulavula translates to English → Claude replies → Vulavula translates back to isiZulu. Multi-turn, chat-style UI.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in VULAVULA_API_TOKEN and ANTHROPIC_API_KEY
python app.py
```

Requires `ffmpeg` on the PATH (browser records webm/opus; Vulavula wants wav).

Open <http://127.0.0.1:5050>.

## Deploy to Fly.io

```bash
brew install flyctl
fly auth login
fly launch --no-deploy --copy-config --name vulavula-claude-chat
fly secrets set VULAVULA_API_TOKEN=... ANTHROPIC_API_KEY=...
fly deploy
```

The Dockerfile installs `ffmpeg` and serves the app with gunicorn on port 8080.
`fly.toml` is set to `auto_stop_machines = "stop"` so it scales to zero when idle.

## Pieces

- `POST https://api.lelapa.ai/v1/transcribe/sync` with `lang_code=zul`, header `X-CLIENT-TOKEN`
- `POST https://api.lelapa.ai/v1/translate/process` with `zul_Latn` ↔ `eng_Latn`
- Anthropic SDK, model `claude-opus-4-7`
