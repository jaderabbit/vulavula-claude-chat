FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    # isiZulu TTS: call the OmniVoice Gradio Space over HTTP (no local model,
    # so this fits the 512MB Fly VM). Point OMNIVOICE_SPACE_URL at your own
    # always-on Space for the live demo — see TTS_NOTES.md.
    TTS_BACKEND=omnivoice \
    OMNIVOICE_SPACE_URL=https://k2-fsa-omnivoice.hf.space \
    OMNIVOICE_MODE=design \
    OMNIVOICE_LANG=Zulu

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn

COPY app.py index.html ./

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "180", "app:app"]
