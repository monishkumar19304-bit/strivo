# Strivo: one small Python service. Run it with host networking so it can find your boxes:
#   docker run -d --name strivo --network host --restart unless-stopped -v strivo-data:/data ghcr.io/nickdnj/strivo
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STRIVO_DATA=/data \
    STRIVO_PORT=8091

# tzdata: so history days follow TZ (e.g. -e TZ=America/New_York)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . \
 && useradd --system --uid 1000 --home /data strivo \
 && mkdir -p /data && chown strivo /data

USER strivo
VOLUME /data
EXPOSE 8091
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/setup' % os.environ.get('STRIVO_PORT','8091'), timeout=4)"
CMD ["strivo", "serve"]
