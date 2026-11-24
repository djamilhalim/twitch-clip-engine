FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Berlin

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-montserrat fonts-noto-color-emoji ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Whisper-Modell beim Build vorladen, damit der erste Clip nicht wartet
ARG WHISPER_MODEL=small
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"

COPY engine ./engine
COPY config.example.yaml .

# config.yaml, .env und data/ werden per Volume eingebunden (siehe docker-compose.yml)
CMD ["python", "-m", "engine", "run"]
