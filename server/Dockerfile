FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py ./

# Persistent state lives in /data (SQLite, encryption key, auth token) and the
# bots' confined workspace in /workspace. Both are volumes in docker-compose.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /workspace \
    && chown -R appuser:appuser /data /workspace /app
USER appuser

ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data \
    WORKSPACE_ROOT=/workspace \
    COMPUTER_PROVIDER=fake

EXPOSE 8000

# --proxy-headers lets the API log the real client address when Caddy is in
# front. Loopback session bootstrap is disabled whenever APP_AUTH_TOKEN is set,
# so trusting the forwarded address never grants access on its own.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
