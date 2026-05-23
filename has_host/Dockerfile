FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Taipei
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir \
        ultralytics==8.4.46 \
        opencv-python-headless==4.13.0.92 \
        "supabase>=2.10.0"

COPY nas_roots.py /app/nas_roots.py
COPY detect_has_host_nas.py /app/detect_has_host.py
COPY tag_short_videos_nas.py /app/tag_short_videos_nas.py
COPY yolov8n.pt /app/yolov8n.pt
COPY .env /app/.env

CMD ["python", "/app/detect_has_host.py"]
