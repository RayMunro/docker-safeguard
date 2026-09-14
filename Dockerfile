FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        zstd \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/app/static/vendor \
    && curl -fsSL https://cdn.jsdelivr.net/npm/htmx.org@2.0.3/dist/htmx.min.js -o /app/app/static/vendor/htmx.min.js \
    && curl -fsSL https://cdn.jsdelivr.net/npm/alpinejs@3.14.3/dist/cdn.min.js -o /app/app/static/vendor/alpine.min.js

COPY app ./app

ENV DATA_DIR=/config \
    TEMPLATES_USER_DIR=/unraid-templates \
    SHARES_ROOT=/mnt/user \
    DISKS_ROOT=/mnt/disks \
    REMOTES_ROOT=/mnt/remotes \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
