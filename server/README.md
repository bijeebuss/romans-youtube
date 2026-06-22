# Roman's Tube — server

A small home server that powers the Apple TV app. It must run on the **same
local network** as the Apple TV (YouTube stream URLs are tied to the requesting
IP, so extracting on the LAN and playing on the LAN "just works").

What it does:

1. **Feed** — pulls each whitelisted channel's uploads via YouTube's free RSS
   feeds, merges them into one chronological list, and **filters out Shorts**
   plus videos that are not playable from the server's location.
2. **Stream** — extracts a directly-playable MP4 URL on demand with `yt-dlp`.
3. **Admin** — a password-protected web page at `/admin` to add/remove channels.

## Run with Docker (recommended for the Raspberry Pi)

```bash
cd server
# Edit docker-compose.yml and change ADMIN_PASSWORD first!
docker compose up -d --build
```

That's it. The service comes back automatically after reboots
(`restart: unless-stopped`). Your channel whitelist lives in a named volume
(`romanstube-data`) so it survives rebuilds.

Check it's alive (replace with the Pi's IP):

```bash
curl http://PI_IP:8000/health
```

Then open `http://PI_IP:8000/admin` in a browser, log in with the user/password
from the compose file, and add your approved channels.

Updating yt-dlp (do this occasionally — YouTube changes break old versions):

```bash
docker compose build --no-cache && docker compose up -d
```

## Run without Docker (e.g. quick test on your Mac)

Needs **ffmpeg** on your PATH (`brew install ffmpeg`).

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ADMIN_PASSWORD=changeme python3 app.py
# → http://localhost:8000/admin
```

Note: `python app.py` uses Flask's single-process dev server, which is fine for
testing. In production the Docker image runs gunicorn with **one worker + many
threads** — important, because the live HLS sessions are held in memory and
every request for a video must reach the same process.

## Self-test (run this before touching the Apple TV)

`selftest.py` proves the whole playback chain works — tools present, yt-dlp can
resolve formats, ffmpeg actually produces HLS segments, and the output is H.264
at the expected resolution. It exits 0 on success, 1 on failure.

```bash
# Inside the container (recommended — that's where yt-dlp/ffmpeg live):
docker compose exec romanstube python3 selftest.py

# Test with one of YOUR videos (ideally a 1080p one, to exercise transcoding):
docker compose exec romanstube python3 selftest.py "https://youtu.be/VIDEO_ID"

# End-to-end against the running HTTP server, exactly as the Apple TV does:
docker compose exec romanstube python3 selftest.py --http http://localhost:8000 VIDEO_ID
```

Sample of a healthy run:

```
Checking required tools
  ✅ ffmpeg found
  ✅ ffprobe found
  ✅ yt-dlp importable
Resolving formats for dQw4w9WgXcQ
  ✅ video: 1080p  vp9
  ✅ audio: opus  (separate track)
  ⚠️  video path: TRANSCODE to H.264 (preset veryfast) — normal for VP9/AV1 1080p
Muxing to HLS with ffmpeg
  ✅ first playlist written in 3.4s
  ✅ segment produced: seg_00000.ts (612 KB)
Verifying the segment with ffprobe
  ✅ decoded by ffprobe: h264 1920x1080
  ✅ ALL GOOD — the server can stream this video to the Apple TV.
```

If the **format-resolution** step is what fails, your `yt-dlp` is almost
certainly stale — rebuild: `docker compose build --no-cache && docker compose up -d`.

Some YouTube videos also require yt-dlp's JavaScript challenge solver. The
Docker image includes `nodejs`, and Python dependencies include `yt-dlp-ejs` so
the solver is available without downloading it at runtime. The server enables
`YTDLP_JS_RUNTIMES=node` and `YTDLP_REMOTE_COMPONENTS=ejs:github` by default.
You normally do not need to set these yourself.

The feed also probes video playability with yt-dlp after removing Shorts. Definite
unplayable results such as geo-blocked, private, removed, or age-restricted
videos are cached in `playability_cache.json` and hidden from feeds. Tune this
with `PLAYABILITY_CACHE_TTL` and `PLAYABILITY_PROBE_WORKERS` if needed.

## Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness + channel count |
| `GET /api/feed` | none | merged chronological feed (JSON) used by the app |
| `GET /api/feed?refresh=1` | none | force a rebuild from RSS |
| `GET /api/stream/<videoId>` | none | resolve a playable stream URL |
| `GET /admin` | basic auth | manage the channel whitelist |

The API endpoints have no auth because the box lives on your private LAN. If you
ever expose it beyond the LAN, put it behind a reverse proxy / VPN.

## Adding channels

On the `/admin` page paste any of:

- a channel URL — `https://www.youtube.com/@NatGeo`
- an `@handle` — `@NatGeo`
- a raw channel id — `UCpVm7bg6pXKo1Pr6k5kxG9A`

The server resolves it to the canonical `UC…` id and remembers the name.

## Notes / limits

- **Quality**: up to **1080p**. The server resolves the best video+audio at or
  below `MAX_HEIGHT` and muxes them into HLS with ffmpeg. When YouTube offers
  H.264 at that resolution it's **copied** (cheap); when 1080p is only VP9/AV1
  it's **transcoded** to H.264 in real time (an 8-core desktop CPU handles this
  comfortably; tune `X264_PRESET` if needed). Set `MAX_HEIGHT=720` to force
  copy-only / lighter load.
- **First-play latency**: a few seconds while ffmpeg produces the first HLS
  segments, then playback starts and the rest streams as it's produced — the
  whole video is never pre-downloaded.
- **Shorts**: detected by probing `youtube.com/shorts/<id>` (200 = Short, a
  redirect = normal video). The verdict is cached per video id. They're removed
  from the feed and also refused by `/api/stream` as a safety net.
- **Age-restricted / private videos** may fail to extract; they just won't play.
