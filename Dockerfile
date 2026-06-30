# Stateless OMR extraction API (no frontend, no persistence)
FROM python:3.11-slim

# libglib2.0-0 and libgomp1 are required by opencv-python-headless
# libgl1 is NOT needed with the headless variant
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

# WORKDIR must be backend/ so Python imports resolve correctly:
# "from omr.config import OMRConfig", etc.
WORKDIR /app/backend
ENV PORT=7860
EXPOSE 7860

# WEB_CONCURRENCY = OS processes (true multi-core); OMR_MAX_CONCURRENCY (read in
# app.py) bounds in-process threaded jobs per worker. Defaults: 1 worker, and
# in-process concurrency = CPU count. Set OMR_API_KEYS to enable access.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-1}"]
