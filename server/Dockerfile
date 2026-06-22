# Roman's Tube server — small, multi-arch (works on Raspberry Pi arm64 and x86).
FROM python:3.12-slim

ARG NODE_VERSION=22.21.1

# ffmpeg muxes/transcodes streams, Node 22 lets yt-dlp solve YouTube JS
# challenges for videos that otherwise look falsely unavailable, and tini gives
# clean signal handling. Debian's nodejs package is currently Node 20, but
# yt-dlp's Node runtime requires Node 22+.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg tini xz-utils \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
        amd64) node_arch="x64" ;; \
        arm64) node_arch="arm64" ;; \
        *) echo "Unsupported Node architecture: $arch" >&2; exit 1 ;; \
    esac \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    && tar -xJf "node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" -C /usr/local --strip-components=1 \
    && rm "node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    && node --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run under gunicorn for a bit more robustness than Flask's dev server.
RUN pip install --no-cache-dir gunicorn==22.0.0

EXPOSE 8000

ENV PORT=8000 \
    DATA_DIR=/data

# channels.json, the shorts cache, and live HLS sessions live in /data (a
# mounted volume) so the whitelist survives container rebuilds.
#
# One worker + threads (not multiple workers): the HLS session map is in-memory,
# so all requests for a video must hit the same process. Threads handle the
# concurrent playlist + segment requests fine. Long timeout because segment
# requests stay open while ffmpeg is still producing.
ENTRYPOINT ["tini", "--"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "16", "--timeout", "300", "app:app"]
