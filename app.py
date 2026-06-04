import os
import io
import subprocess
import traceback
import requests
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

VULAVULA_TOKEN = os.environ["VULAVULA_API_TOKEN"]
VULAVULA_BASE = "https://api.lelapa.ai/v1"
VULAVULA_HEADERS = {"X-CLIENT-TOKEN": VULAVULA_TOKEN}

STT_LANG = "zul"
TRANSLATE_SRC = "zul_Latn"
TRANSLATE_TGT = "eng_Latn"

anthropic_client = Anthropic()
CLAUDE_MODEL = "claude-opus-4-7"

app = Flask(__name__, static_folder=None)


def to_wav(audio_bytes: bytes) -> bytes:
    """Convert any browser-recorded audio (webm/opus, ogg, mp4) to 16 kHz mono WAV via ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
        input=audio_bytes, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode(errors='replace')}")
    return proc.stdout


def transcribe(audio_bytes: bytes, filename: str, mimetype: str) -> str:
    wav_bytes = to_wav(audio_bytes)
    r = requests.post(
        f"{VULAVULA_BASE}/transcribe/sync",
        headers=VULAVULA_HEADERS,
        params={"lang_code": STT_LANG},
        files={"file": ("speech.wav", io.BytesIO(wav_bytes), "audio/wav")},
        timeout=120,
    )
    if not r.ok:
        raise RuntimeError(f"vulavula transcribe {r.status_code}: {r.text}")
    data = r.json()
    if isinstance(data, dict):
        for key in ("transcription_text", "transcription", "text"):
            if key in data and isinstance(data[key], str):
                return data[key]
        if "results" in data and data["results"]:
            first = data["results"][0]
            if isinstance(first, dict) and "transcription" in first:
                return first["transcription"]
    return str(data)


def translate(text: str, source: str, target: str) -> str:
    r = requests.post(
        f"{VULAVULA_BASE}/translate/process",
        headers={**VULAVULA_HEADERS, "Content-Type": "application/json"},
        json={"input_text": text, "source_lang": source, "target_lang": target},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"vulavula translate {r.status_code}: {r.text}")
    data = r.json()
    translations = data.get("translation") or []
    if translations and isinstance(translations[0], dict):
        return translations[0].get("translated_text", "")
    return ""


def ask_claude(history: list, prompt_english: str) -> str:
    messages = [{"role": m["role"], "content": m["content"]} for m in history if m.get("content")]
    messages.append({"role": "user", "content": prompt_english})
    msg = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=(
            "You are a helpful assistant in a voice chat. The user is speaking isiZulu; "
            "their message has been translated to English. Reply in clear, concise English "
            "(2-4 sentences) so it translates back cleanly to isiZulu. Keep the conversation natural."
        ),
        messages=messages,
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


@app.post("/api/voice")
def voice():
    f = request.files.get("audio")
    if not f:
        return jsonify({"error": "no audio file"}), 400
    audio_bytes = f.read()
    import json
    try:
        history = json.loads(request.form.get("history") or "[]")
    except json.JSONDecodeError:
        history = []
    stage = "transcribe"
    try:
        zulu_text = transcribe(audio_bytes, f.filename or "audio.webm", f.mimetype or "audio/webm")
        if not zulu_text.strip():
            return jsonify({"error": "empty transcription", "stage": stage, "zulu_in": zulu_text}), 422
        stage = "translate_in"
        english_in = translate(zulu_text, TRANSLATE_SRC, TRANSLATE_TGT)
        stage = "claude"
        english_out = ask_claude(history, english_in)
        stage = "translate_out"
        zulu_out = translate(english_out, TRANSLATE_TGT, TRANSLATE_SRC)
    except Exception as e:
        app.logger.exception("voice pipeline failed at stage=%s", stage)
        return jsonify({
            "error": str(e),
            "type": type(e).__name__,
            "stage": stage,
            "trace": traceback.format_exc(),
        }), 500
    return jsonify({
        "zulu_in": zulu_text,
        "english_in": english_in,
        "english_out": english_out,
        "zulu_out": zulu_out,
    })


@app.get("/")
def index():
    return send_from_directory(os.path.dirname(__file__), "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=True)
