import os
import io
import json
import time
import base64
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

# Vulavula returns transcripts unpunctuated and lowercased ("sawubona ngicela
# ungitshele ngezilimi zaseningizimu afrika"). Restoring sentence punctuation and
# casing before translation gives the translator sentence boundaries to work with.
# Set PUNCTUATE_TRANSCRIPT=0 to skip the step (saves a round trip).
PUNCTUATE_TRANSCRIPT = os.environ.get("PUNCTUATE_TRANSCRIPT", "1").lower() not in (
    "0", "false", "off", "no",
)
PUNCTUATE_MODEL = os.environ.get("PUNCTUATE_MODEL", "claude-haiku-4-5")
# output_config.effort exists on the 4.6+ family only; older models (e.g.
# claude-haiku-4-5) reject it with a 400, so only send it where it applies.
_EFFORT_MODELS = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
                  "claude-opus-4-6", "claude-sonnet-5", "claude-sonnet-4-6",
                  "claude-fable-5")

# ---------------------------------------------------------------------------
# Text-to-speech (isiZulu) — OmniVoice over HTTP
# ---------------------------------------------------------------------------
# The reply text (`zulu_out`) is synthesised to speech so the user HEARS the
# answer. We do NOT run a model locally — the app is a thin HTTP client for the
# OmniVoice Gradio Space (600+ languages incl. isiZulu). See TTS_NOTES.md.
#
# TTS_BACKEND:
#   "omnivoice" -> call the OmniVoice Gradio API (default).
#   "off"       -> disable TTS entirely (pipeline still returns text).
#
# OmniVoice is a Gradio app; its API is the queue protocol, not plain REST:
#   POST  {space}/gradio_api/call/{api_name}   {"data": [...]}  -> {"event_id"}
#   GET   {space}/gradio_api/call/{api_name}/{event_id}         -> SSE result
# The result event carries a FileData dict whose "url" we then download.
#
# Two endpoints exist:
#   "_design_fn" (Voice Design) -> text + language only, no reference clip.
#   "_clone_fn"  (Voice Clone)  -> also needs a reference audio + text (a chosen
#                                  voice). Enabled via OMNIVOICE_MODE=clone.
TTS_BACKEND = os.environ.get("TTS_BACKEND", "omnivoice").lower()

# Base URL of the OmniVoice Gradio Space (public rehearsal space by default;
# point this at your own duplicated always-on Space for the live demo).
OMNIVOICE_SPACE_URL = os.environ.get(
    "OMNIVOICE_SPACE_URL", "https://k2-fsa-omnivoice.hf.space"
).rstrip("/")
# "design" (text+language only) or "clone" (needs a reference voice).
OMNIVOICE_MODE = os.environ.get("OMNIVOICE_MODE", "design").lower()
# api_name defaults from the mode but can be overridden explicitly.
OMNIVOICE_API_NAME = os.environ.get(
    "OMNIVOICE_API_NAME",
    "_clone_fn" if OMNIVOICE_MODE == "clone" else "_design_fn",
).lstrip("/")
# Exact dropdown label/value from the Space config; "Zulu" is a valid choice.
OMNIVOICE_LANG = os.environ.get("OMNIVOICE_LANG", "Zulu")
# Optional HF token (needed if you duplicate the Space as private).
OMNIVOICE_TOKEN = os.environ.get("OMNIVOICE_TOKEN", "")
# Generation knobs (defaults mirror the Space's own defaults).
OMNIVOICE_STEPS = int(os.environ.get("OMNIVOICE_STEPS", "32"))
OMNIVOICE_CFG = float(os.environ.get("OMNIVOICE_CFG", "2.0"))
OMNIVOICE_SPEED = float(os.environ.get("OMNIVOICE_SPEED", "1.0"))
# Voice-clone-only inputs (a URL/path to a short reference clip + its text).
OMNIVOICE_REF_AUDIO = os.environ.get("OMNIVOICE_REF_AUDIO", "")
OMNIVOICE_REF_TEXT = os.environ.get("OMNIVOICE_REF_TEXT", "")
OMNIVOICE_INSTRUCT = os.environ.get("OMNIVOICE_INSTRUCT", "")
# How long to keep polling a cold/queued Space before giving up (seconds).
OMNIVOICE_TIMEOUT = int(os.environ.get("OMNIVOICE_TIMEOUT", "180"))

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


PUNCTUATE_SYSTEM = (
    "You restore punctuation and capitalisation in isiZulu speech transcripts.\n"
    "Rules:\n"
    "- Keep every word exactly as given. Do not translate, correct spelling, "
    "reword, add words, or remove words.\n"
    "- Only add or fix punctuation (. , ? !) and letter case, including "
    "capitalising proper nouns and the isiZulu noun-prefix pattern (e.g. "
    "'ningizimu afrika' -> 'iNingizimu Afrika').\n"
    "- Output only the corrected transcript, with no preamble, quotes or "
    "explanation."
)


def punctuate(text_zulu: str) -> str:
    """Restore punctuation/casing on a raw isiZulu transcript.

    Best-effort: any failure, or output that has drifted too far from the input,
    falls back to the original text so the pipeline is never blocked by it.
    """
    if not PUNCTUATE_TRANSCRIPT or not text_zulu.strip():
        return text_zulu
    try:
        kwargs = {}
        if PUNCTUATE_MODEL in _EFFORT_MODELS:
            # Punctuation needs no deliberation; low effort keeps latency down.
            kwargs["output_config"] = {"effort": "low"}
        msg = anthropic_client.messages.create(
            model=PUNCTUATE_MODEL,
            max_tokens=1024,
            system=PUNCTUATE_SYSTEM,
            messages=[{"role": "user", "content": text_zulu}],
            **kwargs,
        )
        out = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
        # Guard against the model paraphrasing or commenting instead of
        # punctuating: the word count should barely move.
        n_in, n_out = len(text_zulu.split()), len(out.split())
        if not out or not (0.7 <= n_out / max(n_in, 1) <= 1.4):
            app.logger.warning(
                "punctuate: rejected output (%d words in, %d out)", n_in, n_out
            )
            return text_zulu
        return out
    except Exception:
        app.logger.exception("punctuate failed (using raw transcript)")
        return text_zulu


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


# ---------------------------------------------------------------------------
# Text-to-speech — OmniVoice Gradio Space (HTTP client)
# ---------------------------------------------------------------------------
_EXT_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
}


def _mime_for(url: str, content_type: str = "") -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("audio/"):
        return ct
    for ext, mime in _EXT_MIME.items():
        if ext in url.lower():
            return mime
    return "audio/wav"


def _omnivoice_payload(text_zulu: str) -> list:
    """Build the ordered `data` array for the selected OmniVoice endpoint.

    Order comes from the Space's /gradio_api/info. Voice Design takes text +
    language (+ generation knobs); Voice Clone additionally needs a reference
    audio clip and its transcript.
    """
    if OMNIVOICE_MODE == "clone":
        ref = OMNIVOICE_REF_AUDIO
        if ref and ref.startswith(("http://", "https://")):
            ref = {"path": ref, "url": ref, "meta": {"_type": "gradio.FileData"}}
        elif ref:
            ref = {"path": ref, "meta": {"_type": "gradio.FileData"}}
        else:
            ref = None
        # _clone_fn: text, language, ref_audio, ref_text, instruct, steps, cfg,
        #            denoise, speed, duration, preprocess, postprocess
        return [
            text_zulu, OMNIVOICE_LANG, ref, OMNIVOICE_REF_TEXT, OMNIVOICE_INSTRUCT,
            OMNIVOICE_STEPS, OMNIVOICE_CFG, True, OMNIVOICE_SPEED, None, True, True,
        ]
    # _design_fn: text, language, steps, cfg, denoise, speed, duration,
    #             preprocess, postprocess, gender, age, pitch, style,
    #             english_accent, chinese_dialect
    return [
        text_zulu, OMNIVOICE_LANG, OMNIVOICE_STEPS, OMNIVOICE_CFG, True,
        OMNIVOICE_SPEED, None, True, True, "Auto", "Auto", "Auto", "Auto",
        "Auto", "Auto",
    ]


def _first_audio_from_event(data):
    """Pull the audio FileData (a dict with a url/path) out of a Gradio result
    event payload, which is a list of the endpoint's outputs."""
    if isinstance(data, dict):
        candidates = [data]
    elif isinstance(data, (list, tuple)):
        candidates = list(data)
    else:
        return None
    for item in candidates:
        if isinstance(item, dict) and (item.get("url") or item.get("path")):
            return item
    return None


def _synthesize_omnivoice(text_zulu: str):
    """Call the OmniVoice Gradio queue API and return (audio_bytes, mimetype).

    Protocol: POST the data array to /gradio_api/call/{api_name} to get an
    event_id, then GET the SSE result stream and download the returned file.
    Retries with backoff while the Space is cold/queued.
    """
    headers = {"Content-Type": "application/json"}
    if OMNIVOICE_TOKEN:
        headers["Authorization"] = f"Bearer {OMNIVOICE_TOKEN}"
    call_url = f"{OMNIVOICE_SPACE_URL}/gradio_api/call/{OMNIVOICE_API_NAME}"
    payload = {"data": _omnivoice_payload(text_zulu)}

    deadline = time.time() + OMNIVOICE_TIMEOUT
    delay = 2.0
    last_err = None
    while time.time() < deadline:
        try:
            post = requests.post(call_url, headers=headers, json=payload, timeout=30)
            if post.status_code in (429, 503):  # cold boot / queue full
                raise RuntimeError(f"space busy {post.status_code}")
            if not post.ok:
                raise RuntimeError(f"omnivoice call {post.status_code}: {post.text[:300]}")
            event_id = post.json().get("event_id")
            if not event_id:
                raise RuntimeError(f"no event_id in response: {post.text[:200]}")

            # Stream the result (SSE): lines alternate "event: <name>" /
            # "data: <json>". We want the payload of the "complete" event.
            get_url = f"{call_url}/{event_id}"
            remaining = max(5, int(deadline - time.time()))
            stream = requests.get(get_url, headers=headers, stream=True, timeout=remaining)
            if not stream.ok:
                raise RuntimeError(f"omnivoice result {stream.status_code}")
            event_name, audio_meta = None, None
            for raw in stream.iter_lines(decode_unicode=True):
                if raw is None or raw == "":
                    continue
                if raw.startswith("event:"):
                    event_name = raw[len("event:"):].strip()
                elif raw.startswith("data:"):
                    body = raw[len("data:"):].strip()
                    if event_name == "error":
                        raise RuntimeError(f"omnivoice error event: {body[:300]}")
                    try:
                        parsed = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    if event_name in ("complete", "generating"):
                        found = _first_audio_from_event(parsed)
                        if found:
                            audio_meta = found
                        if event_name == "complete":
                            break
            if not audio_meta:
                raise RuntimeError("no audio in OmniVoice result stream")

            # Resolve the file URL and download the actual bytes.
            file_url = audio_meta.get("url")
            if not file_url:
                path = audio_meta.get("path", "")
                file_url = f"{OMNIVOICE_SPACE_URL}/gradio_api/file={path}"
            elif file_url.startswith("/"):
                file_url = f"{OMNIVOICE_SPACE_URL}{file_url}"
            fr = requests.get(file_url, headers=headers, timeout=60)
            if not fr.ok or not fr.content:
                raise RuntimeError(f"omnivoice file fetch {fr.status_code}")
            return fr.content, _mime_for(file_url, fr.headers.get("Content-Type", ""))
        except Exception as e:  # transient: back off and retry until deadline
            last_err = e
            if time.time() + delay >= deadline:
                break
            time.sleep(delay)
            delay = min(delay * 1.7, 20.0)
    raise RuntimeError(f"omnivoice synthesis failed: {last_err}")


def synthesize_audio(text_zulu: str):
    """Turn isiZulu text into (audio_bytes, mimetype).

    Backend is selected by TTS_BACKEND. Raises on failure; the caller degrades
    gracefully to text-only.
    """
    text_zulu = (text_zulu or "").strip()
    if not text_zulu:
        raise RuntimeError("empty text for synthesis")
    if TTS_BACKEND == "omnivoice":
        return _synthesize_omnivoice(text_zulu)
    raise RuntimeError(f"unknown TTS_BACKEND={TTS_BACKEND!r}")


def synthesize(text_zulu: str) -> bytes:
    """Convenience wrapper returning just the audio bytes."""
    audio, _ = synthesize_audio(text_zulu)
    return audio


def synthesize_data_uri(text_zulu: str):
    """Best-effort synthesis to a base64 data URI. Returns None on any failure
    so a TTS problem can never break the (text) voice pipeline."""
    if TTS_BACKEND == "off":
        return None
    try:
        audio, mime = synthesize_audio(text_zulu)
        if not audio:
            return None
        b64 = base64.b64encode(audio).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        app.logger.exception("tts synthesis failed (returning text-only)")
        return None


@app.post("/api/voice")
def voice():
    f = request.files.get("audio")
    if not f:
        return jsonify({"error": "no audio file"}), 400
    audio_bytes = f.read()
    try:
        history = json.loads(request.form.get("history") or "[]")
    except json.JSONDecodeError:
        history = []
    stage = "transcribe"
    try:
        zulu_text = transcribe(audio_bytes, f.filename or "audio.webm", f.mimetype or "audio/webm")
        if not zulu_text.strip():
            return jsonify({"error": "empty transcription", "stage": stage, "zulu_in": zulu_text}), 422
        stage = "punctuate"
        zulu_text = punctuate(zulu_text)
        stage = "translate_in"
        english_in = translate(zulu_text, TRANSLATE_SRC, TRANSLATE_TGT)
        stage = "claude"
        english_out = ask_claude(history, english_in)
        stage = "translate_out"
        zulu_out = translate(english_out, TRANSLATE_TGT, TRANSLATE_SRC)
        stage = "tts"
        audio_out = synthesize_data_uri(zulu_out)
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
        "audio_out": audio_out,  # base64 data URI of the spoken isiZulu reply, or null
    })


@app.get("/")
def index():
    return send_from_directory(os.path.dirname(__file__), "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=True)
