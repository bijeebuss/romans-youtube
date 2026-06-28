"""
Roman's Tube — home server.

Runs on an always-on box on the SAME network as the Apple TV. It does the
things tvOS can't:

  1. Builds one chronological feed from a whitelist of YouTube channels,
     using YouTube's free per-channel RSS feeds (no API key, no quota).
     Shorts are filtered out.
  2. On demand, resolves the best video+audio (up to 1080p) with yt-dlp and
     muxes them into an HLS stream with ffmpeg, which the tvOS app plays
     natively in AVPlayer. H.264 is copied; VP9/AV1 is transcoded.

There is also a tiny password-protected web admin page at /admin for adding
and removing approved channels.

Run (needs ffmpeg on PATH):
    ADMIN_PASSWORD=changeme python3 app.py
"""

import functools
from html import unescape as html_unescape
from importlib.metadata import PackageNotFoundError, version
import json
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse

import requests
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from yt_dlp import YoutubeDL, version as ytdlp_version

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
# Persisted data (whitelist + shorts cache) lives in DATA_DIR so it survives
# container rebuilds when mounted as a volume. Falls back to the app dir.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHANNELS_FILE = DATA_DIR / "channels.json"
PROFILES_FILE = DATA_DIR / "profiles.json"
PROFILE_PICTURES_DIR = DATA_DIR / "profile_pictures"
SHORTS_CACHE_FILE = DATA_DIR / "shorts_cache.json"
PLAYABILITY_CACHE_FILE = DATA_DIR / "playability_cache.json"
PROFILE_PICTURES_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
APP_VERSION = os.environ.get("APP_VERSION", "2026-06-22.9")

# How long to trust a cached feed before rebuilding from RSS (seconds).
FEED_TTL = int(os.environ.get("FEED_TTL", "600"))  # 10 minutes
# How many of the newest videos to keep per channel.
PER_CHANNEL_LIMIT = int(os.environ.get("PER_CHANNEL_LIMIT", "15"))
# How many items the merged feed returns.
FEED_LIMIT = int(os.environ.get("FEED_LIMIT", "200"))
# How many channel RSS feeds to fetch in parallel while rebuilding the feed.
FEED_RSS_WORKERS = max(1, int(os.environ.get("FEED_RSS_WORKERS", "8")))

# Playback / HLS settings.
MAX_HEIGHT = int(os.environ.get("MAX_HEIGHT", "1080"))   # 1080, 720, etc.
HLS_ROOT = DATA_DIR / "hls"
HLS_SEGMENT_SECONDS = int(os.environ.get("HLS_SEGMENT_SECONDS", "4"))
HLS_IDLE_TIMEOUT = int(os.environ.get("HLS_IDLE_TIMEOUT", "600"))  # reap idle sessions
INDEX_WAIT_SECONDS = 25      # how long /api/stream waits for the first playlist
X264_PRESET = os.environ.get("X264_PRESET", "veryfast")  # used only when transcoding

YT_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
HTTP_TIMEOUT = 10
SHORTS_PROBE_TIMEOUT = float(os.environ.get("SHORTS_PROBE_TIMEOUT", "3"))
SHORTS_PROBE_WORKERS = int(os.environ.get("SHORTS_PROBE_WORKERS", "12"))
PLAYABILITY_CACHE_TTL = int(os.environ.get("PLAYABILITY_CACHE_TTL", "86400"))
PLAYABILITY_PROBE_WORKERS = int(os.environ.get("PLAYABILITY_PROBE_WORKERS", "3"))
CHANNEL_ICON_SIZE = int(os.environ.get("CHANNEL_ICON_SIZE", "176"))
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CHANNEL_ID_RE = re.compile(r"UC[\w-]{22}")
PROFILE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
LEGACY_PROFILE_ID = "default"
YTDLP_JS_RUNTIMES = [
    item.strip()
    for item in os.environ.get("YTDLP_JS_RUNTIMES", "node").split(",")
    if item.strip()
]
YTDLP_REMOTE_COMPONENTS = [
    item.strip()
    for item in os.environ.get("YTDLP_REMOTE_COMPONENTS", "ejs:github").split(",")
    if item.strip()
]

app = Flask(__name__)

# --------------------------------------------------------------------------
# Small JSON-file helpers (thread-safe enough for a single-family server)
# --------------------------------------------------------------------------

_lock = threading.Lock()


class _QuietYTDLPLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _ytdlp_opts(opts):
    merged = {
        "js_runtimes": {name: {} for name in YTDLP_JS_RUNTIMES},
        "logger": _QuietYTDLPLogger(),
        "remote_components": YTDLP_REMOTE_COMPONENTS,
    }
    merged.update(opts)
    return merged


def _package_version(package: str):
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _tool_version(command: str):
    path = shutil.which(command)
    if not path:
        return None
    try:
        proc = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return "present"
    return (proc.stdout or proc.stderr or "present").splitlines()[0]


def _profile_slug(name: str):
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-_")
    return slug[:40] or "profile"


def _default_profile():
    legacy_channels = _load_json(CHANNELS_FILE, [])
    if not isinstance(legacy_channels, list):
        legacy_channels = []
    return {
        "id": LEGACY_PROFILE_ID,
        "name": "Default",
        "picture": "",
        "channels": legacy_channels,
    }


def _normalize_profile(profile):
    if not isinstance(profile, dict):
        return None
    profile_id = str(profile.get("id") or "").strip().lower()
    if not PROFILE_ID_RE.fullmatch(profile_id):
        return None
    name = str(profile.get("name") or profile_id).strip() or profile_id
    channels = profile.get("channels") if isinstance(profile.get("channels"), list) else []
    return {
        "id": profile_id,
        "name": name,
        "picture": str(profile.get("picture") or ""),
        "channels": channels,
    }


def load_profiles():
    raw = _load_json(PROFILES_FILE, None)
    profiles = raw.get("profiles") if isinstance(raw, dict) else raw
    if isinstance(profiles, list):
        normalized = [p for p in (_normalize_profile(profile) for profile in profiles) if p]
        if normalized:
            return normalized

    profiles = [_default_profile()]
    save_profiles(profiles)
    return profiles


def save_profiles(profiles):
    with _lock:
        _save_json(PROFILES_FILE, {"profiles": profiles})


def get_profile(profile_id: str | None):
    profile_id = (profile_id or LEGACY_PROFILE_ID).strip().lower()
    return next((profile for profile in load_profiles() if profile["id"] == profile_id), None)


def create_profile(name: str):
    name = name.strip()
    if not name:
        raise ValueError("Profile name is required")

    profiles = load_profiles()
    existing_ids = {profile["id"] for profile in profiles}
    base = _profile_slug(name)
    profile_id = base
    suffix = 2
    while profile_id in existing_ids:
        profile_id = f"{base[:36]}-{suffix}"
        suffix += 1

    profile = {"id": profile_id, "name": name, "picture": "", "channels": []}
    profiles.append(profile)
    save_profiles(profiles)
    return profile


def rename_profile(profile_id: str, name: str):
    name = name.strip()
    if not name:
        raise ValueError("Profile name is required")

    profiles = load_profiles()
    for profile in profiles:
        if profile["id"] == profile_id:
            profile["name"] = name
            save_profiles(profiles)
            return profile
    raise ValueError("Profile not found")


def save_profile(profile):
    profiles = load_profiles()
    for idx, existing in enumerate(profiles):
        if existing["id"] == profile["id"]:
            profiles[idx] = profile
            save_profiles(profiles)
            return
    profiles.append(profile)
    save_profiles(profiles)


def load_channels(profile_id: str | None = None):
    """Returns a profile's list of {'id': 'UC...', 'name': '...'} dicts."""
    profile = get_profile(profile_id)
    return list(profile.get("channels", [])) if profile else []


def save_channels(channels, profile_id: str | None = None):
    profile_id = (profile_id or LEGACY_PROFILE_ID).strip().lower()
    profiles = load_profiles()
    for profile in profiles:
        if profile["id"] == profile_id:
            profile["channels"] = channels
            with _lock:
                _save_json(PROFILES_FILE, {"profiles": profiles})
                if profile_id == LEGACY_PROFILE_ID:
                    _save_json(CHANNELS_FILE, channels)
            return
    raise ValueError("Profile not found")


def admin_channels(profile_id: str | None = None):
    channels = load_channels(profile_id)
    changed = False
    for ch in channels:
        if ch.get("id") and not ch.get("icon"):
            icon = _channel_icon(ch["id"], ch.get("name"))
            if icon:
                ch["icon"] = icon
                changed = True
    if changed:
        save_channels(channels, profile_id)
    return channels


def _profile_picture_for_api(profile):
    picture = _normalize_icon_url(profile.get("picture"))
    if picture:
        return picture
    picture = profile.get("picture") or ""
    if picture.startswith("/"):
        return request.host_url.rstrip("/") + picture
    return None


def _profile_for_api(profile):
    return {
        "id": profile["id"],
        "name": profile["name"],
        "picture": _profile_picture_for_api(profile),
    }


# --------------------------------------------------------------------------
# Shorts detection
# --------------------------------------------------------------------------
# YouTube's RSS uploads feed mixes Shorts in with regular videos. There is no
# duration in the feed, but there is a reliable, cheap signal: requesting
# https://www.youtube.com/shorts/<id> returns 200 for an actual Short and a
# 30x redirect to /watch?v=<id> for a normal video. We cache the verdict per
# video id so we only pay this once per video, ever.

_shorts_cache = _load_json(SHORTS_CACHE_FILE, {})
_shorts_cache_lock = threading.Lock()


def is_short(video_id: str) -> bool:
    with _shorts_cache_lock:
        if video_id in _shorts_cache:
            return _shorts_cache[video_id]
    verdict = _probe_short(video_id)
    with _shorts_cache_lock:
        _shorts_cache[video_id] = verdict
        # Persist opportunistically; small file, infrequent new ids.
        try:
            _save_json(SHORTS_CACHE_FILE, _shorts_cache)
        except OSError:
            pass
    return verdict


def _probe_short(video_id: str) -> bool:
    url = f"https://www.youtube.com/shorts/{video_id}"
    try:
        r = requests.get(
            url,
            allow_redirects=False,
            stream=True,  # don't download the body
            timeout=SHORTS_PROBE_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        r.close()
        # 200 => it really is a Short. 30x => normal video (redirects to /watch).
        return r.status_code == 200
    except requests.RequestException:
        # If we can't tell, err on the side of showing it (not a Short).
        return False


# --------------------------------------------------------------------------
# Playability detection
# --------------------------------------------------------------------------
# RSS can list videos that are geo-blocked, private, removed, or age-gated from
# this server's location. Cache yt-dlp's verdict so the feed does not repeatedly
# show videos that cannot actually be streamed.

_playability_cache = _load_json(PLAYABILITY_CACHE_FILE, {})
_playability_cache_lock = threading.Lock()


def is_playable(video_id: str) -> bool:
    now = time.time()
    with _playability_cache_lock:
        cached = _playability_cache.get(video_id)
        if isinstance(cached, dict):
            checked_at = float(cached.get("checked_at") or 0)
            if now - checked_at < PLAYABILITY_CACHE_TTL:
                return bool(cached.get("ok", True))
        elif isinstance(cached, bool):
            return cached

    ok, reason = _probe_playable(video_id)
    _remember_playability(video_id, ok, reason)
    return ok


def _probe_playable(video_id: str):
    try:
        video, _audio, _info = _resolve_formats(video_id)
        return bool(video and video.get("url")), ""
    except Exception as exc:
        error, status, _detail = _classify_stream_error(exc)
        if status in (403, 410, 451):
            return False, error
        # Unknown extractor/network failures should not hide a video from the
        # feed; the stream endpoint will still report the exact failure on play.
        return True, "probe_failed"


def _remember_playability(video_id: str, ok: bool, reason: str = ""):
    with _playability_cache_lock:
        previous = _playability_cache.get(video_id)
        if isinstance(previous, dict):
            previous_ok = previous.get("ok")
            previous_reason = previous.get("reason")
        else:
            previous_ok = previous if isinstance(previous, bool) else None
            previous_reason = ""

        _playability_cache[video_id] = {
            "ok": ok,
            "reason": reason,
            "checked_at": time.time(),
        }
        try:
            _save_json(PLAYABILITY_CACHE_FILE, _playability_cache)
        except OSError:
            pass
    if not ok and (previous_ok is not False or previous_reason != reason):
        app.logger.warning(
            "marked video %s as unplayable: %s",
            video_id,
            reason or "unknown",
        )


def _filter_unplayable_videos(videos):
    if not videos:
        return videos
    worker_count = min(PLAYABILITY_PROBE_WORKERS, len(videos))
    ids = [v["id"] for v in videos]
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        playable_by_id = dict(zip(ids, executor.map(is_playable, ids)))
    return [v for v in videos if playable_by_id.get(v["id"], True)]


def _classify_stream_error(exc):
    text = str(exc)
    lower = text.lower()
    if "not made this video available in your country" in lower:
        return "geo_blocked", 451, "This video is not available in your country."
    if "private video" in lower or "members-only" in lower:
        return "not_playable", 403, "This video is not available to this server."
    if "sign in to confirm your age" in lower or "age-restricted" in lower:
        return "not_playable", 403, "This video is age-restricted."
    if "has been removed" in lower or "video unavailable" in lower:
        return "not_playable", 410, "This video is no longer available."
    return "extract_failed", 502, _short_error_detail(text)


def _short_error_detail(text: str):
    first_line = (text or "Could not load this video.").splitlines()[0].strip()
    if first_line.startswith("ERROR: "):
        first_line = first_line[len("ERROR: "):]
    return first_line[:300]


# --------------------------------------------------------------------------
# Feed building
# --------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

_feed_cache = {}
_feed_lock = threading.Lock()


def _fetch_channel_videos(channel):
    """Fetch & parse one channel's RSS into a list of video dicts."""
    cid = channel["id"]
    try:
        r = requests.get(
            YT_RSS.format(cid=cid),
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return []

    channel_name = channel.get("name") or ""
    name_el = root.find(f"{_ATOM}author/{_ATOM}name")
    if name_el is not None and name_el.text:
        channel_name = name_el.text

    videos = []
    for entry in root.findall(f"{_ATOM}entry")[:PER_CHANNEL_LIMIT]:
        vid_el = entry.find(f"{_YT}videoId")
        title_el = entry.find(f"{_ATOM}title")
        published_el = entry.find(f"{_ATOM}published")
        if vid_el is None or vid_el.text is None:
            continue
        video_id = vid_el.text

        group = entry.find(f"{_MEDIA}group")
        thumb_url = ""
        if group is not None:
            thumb_el = group.find(f"{_MEDIA}thumbnail")
            if thumb_el is not None:
                thumb_url = thumb_el.get("url", "")

        videos.append(
            {
                "id": video_id,
                "title": title_el.text if title_el is not None else "",
                "channelId": cid,
                "channel": channel_name,
                "published": published_el.text if published_el is not None else "",
                "thumbnail": thumb_url or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            }
        )
    return videos


def _published_from_ytdlp_entry(entry):
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    if timestamp:
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError):
            pass

    upload_date = entry.get("upload_date")
    if upload_date:
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError):
            pass

    return ""


def _thumbnail_from_ytdlp_entry(entry, video_id):
    thumbnails = entry.get("thumbnails") or []
    if thumbnails:
        return thumbnails[-1].get("url") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def fetch_channel_videos_page(channel, offset=0, limit=24):
    """Fetch one page from a channel uploads playlist, newest first."""
    cid = channel["id"]
    uploads_playlist = "UU" + cid[2:]
    url = f"https://www.youtube.com/playlist?list={uploads_playlist}"
    opts = _ytdlp_opts(
        {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "playliststart": offset + 1,
            "playlistend": offset + limit,
        }
    )
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return [], False

    videos = []
    for entry in info.get("entries") or []:
        video_id = entry.get("id") or entry.get("url")
        if not video_id:
            continue
        videos.append(
            {
                "id": video_id,
                "title": entry.get("title") or "",
                "channelId": cid,
                "channel": channel.get("name") or entry.get("channel") or entry.get("uploader") or "",
                "published": _published_from_ytdlp_entry(entry),
                "thumbnail": _thumbnail_from_ytdlp_entry(entry, video_id),
            }
        )

    if videos:
        worker_count = min(SHORTS_PROBE_WORKERS, len(videos))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            shorts_by_id = dict(
                zip(
                    [v["id"] for v in videos],
                    executor.map(is_short, [v["id"] for v in videos]),
                )
            )
        videos = [v for v in videos if not shorts_by_id.get(v["id"], False)]
        videos = _filter_unplayable_videos(videos)

    return videos, len(info.get("entries") or []) >= limit


def build_feed(profile_id: str | None = None):
    """Merge all whitelisted channels into one chronological feed (newest first)."""
    total_started = time.perf_counter()
    channels = load_channels(profile_id)
    all_videos = []

    rss_started = time.perf_counter()
    if channels:
        worker_count = min(FEED_RSS_WORKERS, len(channels))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for videos in executor.map(_fetch_channel_videos, channels):
                all_videos.extend(videos)
    rss_seconds = time.perf_counter() - rss_started

    rss_video_count = len(all_videos)
    shorts_seconds = 0.0
    playability_seconds = 0.0
    shorts_filtered_count = 0
    unplayable_filtered_count = 0

    # Drop Shorts.
    if all_videos:
        shorts_started = time.perf_counter()
        worker_count = min(SHORTS_PROBE_WORKERS, len(all_videos))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            shorts_by_id = dict(
                zip(
                    [v["id"] for v in all_videos],
                    executor.map(is_short, [v["id"] for v in all_videos]),
                )
            )
        filtered = [v for v in all_videos if not shorts_by_id.get(v["id"], False)]
        shorts_filtered_count = len(all_videos) - len(filtered)
        shorts_seconds = time.perf_counter() - shorts_started

        playability_started = time.perf_counter()
        playable = _filter_unplayable_videos(filtered)
        unplayable_filtered_count = len(filtered) - len(playable)
        playability_seconds = time.perf_counter() - playability_started
        filtered = playable
    else:
        filtered = []

    def sort_key(v):
        try:
            return datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.min.replace(tzinfo=timezone.utc)

    filtered.sort(key=sort_key, reverse=True)
    result = filtered[:FEED_LIMIT]
    app.logger.info(
        "built feed profile=%s channels=%d rss_videos=%d shorts_filtered=%d "
        "unplayable_filtered=%d returned=%d timings rss=%.2fs shorts=%.2fs "
        "playability=%.2fs total=%.2fs",
        (profile_id or LEGACY_PROFILE_ID),
        len(channels),
        rss_video_count,
        shorts_filtered_count,
        unplayable_filtered_count,
        len(result),
        rss_seconds,
        shorts_seconds,
        playability_seconds,
        time.perf_counter() - total_started,
    )
    return result


def get_feed(profile_id: str | None = None, force=False):
    profile_id = (profile_id or LEGACY_PROFILE_ID).strip().lower()
    with _feed_lock:
        cache = _feed_cache.setdefault(profile_id, {"built_at": 0.0, "items": []})
        fresh = (time.time() - cache["built_at"]) < FEED_TTL
        if cache["items"] and fresh and not force:
            return cache["items"]
    # Build outside the lock (network-bound) then publish.
    items = build_feed(profile_id)
    with _feed_lock:
        _feed_cache[profile_id] = {"items": items, "built_at": time.time()}
    return items


# --------------------------------------------------------------------------
# Playback: on-the-fly HLS (up to 1080p)
# --------------------------------------------------------------------------
# 1080p YouTube is delivered as SEPARATE video + audio streams (often VP9/AV1),
# which AVPlayer can't play directly. So we let yt-dlp resolve the best video
# (<= MAX_HEIGHT) and audio URLs, then run ffmpeg to mux them into an HLS stream
# the Apple TV plays natively:
#   • If the video is already H.264 (avc1), we COPY it — basically free.
#   • If it's VP9/AV1 (common at 1080p), we transcode to H.264 — fine on a
#     desktop CPU like the Ryzen 7500, real-time at preset=veryfast.
# Segments are produced live (EVENT playlist) so playback starts in seconds
# without waiting for the whole video to download.

# Active HLS sessions: video_id -> {"proc": Popen|None, "dir": Path, "last": ts}
_hls_sessions = {}
_hls_lock = threading.Lock()


def _format_selector():
    h = MAX_HEIGHT
    return (
        # Prefer H.264 video + AAC audio so we can copy with no transcode.
        f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
        # Otherwise best video at the cap (VP9/AV1) + best audio; we transcode.
        f"bestvideo[height<={h}]+bestaudio/"
        f"best[height<={h}]"
    )


def _resolve_formats(video_id: str):
    """Return (video_fmt, audio_fmt_or_None, info) for the chosen formats."""
    opts = _ytdlp_opts(
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "format": _format_selector(),
            "noplaylist": True,
        }
    )
    url = f"https://www.youtube.com/watch?v={video_id}"
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    rf = info.get("requested_formats")
    if rf:
        video = rf[0]
        audio = rf[1] if len(rf) > 1 else None
    else:
        # A single progressive stream already carrying both tracks.
        video = info
        audio = None
    return video, audio, info


def _ua_for(fmt):
    headers = (fmt or {}).get("http_headers") or {}
    return headers.get("User-Agent", USER_AGENT)


def _build_ffmpeg_cmd(video, audio, out_dir: Path):
    vcodec = (video.get("vcodec") or "").lower()
    acodec = ((audio or video).get("acodec") or "").lower()

    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "warning"]
    cmd += ["-user_agent", _ua_for(video), "-i", video["url"]]
    if audio:
        cmd += ["-user_agent", _ua_for(audio), "-i", audio["url"]]
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]

    # Copy H.264 video as-is; transcode VP9/AV1 → H.264.
    if vcodec.startswith(("avc1", "h264")):
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", X264_PRESET, "-crf", "20",
                "-pix_fmt", "yuv420p", "-g", "120"]

    # Copy AAC audio as-is; transcode anything else (e.g. Opus) → AAC.
    if acodec.startswith(("mp4a", "aac")):
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "160k"]

    cmd += [
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_SECONDS),
        "-hls_playlist_type", "event",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(out_dir / "seg_%05d.ts"),
        str(out_dir / "index.m3u8"),
    ]
    return cmd


def start_hls(video_id: str) -> Path:
    """Ensure an HLS session exists for this video; return its output dir."""
    with _hls_lock:
        out_dir = HLS_ROOT / video_id
        index = out_dir / "index.m3u8"
        sess = _hls_sessions.get(video_id)

        # Reuse a warm session: still encoding, or finished with a full playlist.
        if sess:
            proc = sess["proc"]
            running = proc is not None and proc.poll() is None
            if running or index.exists():
                sess["last"] = time.time()
                return out_dir

        # Start fresh.
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        video, audio, _info = _resolve_formats(video_id)
        if not video or not video.get("url"):
            raise RuntimeError("no_playable_format")
        _remember_playability(video_id, True)

        cmd = _build_ffmpeg_cmd(video, audio, out_dir)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _hls_sessions[video_id] = {"proc": proc, "dir": out_dir, "last": time.time()}
        return out_dir


def _hls_reaper():
    """Delete finished sessions that have been idle for a while."""
    while True:
        time.sleep(60)
        now = time.time()
        with _hls_lock:
            for vid, sess in list(_hls_sessions.items()):
                proc = sess["proc"]
                finished = proc is None or proc.poll() is not None
                if finished and (now - sess["last"]) > HLS_IDLE_TIMEOUT:
                    shutil.rmtree(sess["dir"], ignore_errors=True)
                    _hls_sessions.pop(vid, None)


def _start_background_workers():
    # Clear any stale HLS dirs from a previous run, then start the reaper.
    shutil.rmtree(HLS_ROOT, ignore_errors=True)
    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_hls_reaper, daemon=True).start()


_start_background_workers()


# --------------------------------------------------------------------------
# Channel resolution (paste a URL / @handle / channel id → UC id + name)
# --------------------------------------------------------------------------

def resolve_channel(raw: str):
    """Turn whatever the parent pasted into {'id','name'}. Raises ValueError."""
    raw = raw.strip()
    if not raw:
        raise ValueError("Empty input")

    # Already a channel id?
    if CHANNEL_ID_RE.fullmatch(raw):
        cid = raw
        name = _channel_name_from_rss(cid) or cid
        return {"id": cid, "name": name, "icon": _channel_icon(cid, name)}

    cid = _channel_id_from_url(raw)
    if cid:
        name = _channel_name_from_rss(cid) or cid
        return {"id": cid, "name": name, "icon": _channel_icon(cid, name)}

    # Normalise into a URL we can fetch.
    if raw.startswith("@"):
        url = f"https://www.youtube.com/{raw}"
    elif raw.startswith("http"):
        url = raw
    else:
        url = f"https://www.youtube.com/@{raw}"

    cid, name, icon = _resolve_via_page(url)
    if not cid:
        cid, name, icon = _resolve_via_search(raw, exact_only=True)
    if not cid:
        cid, name, icon = _resolve_via_ytdlp(url)
    if not cid and not _channel_search_query(raw)[1]:
        cid, name, icon = _resolve_via_search(raw)
    if not cid:
        raise ValueError("Could not find a channel id for that input")
    name = name or _channel_name_from_rss(cid) or cid
    return {"id": cid, "name": name, "icon": icon or _channel_icon(cid, name)}


def _channel_id_from_url(raw: str):
    if not raw.startswith("http"):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if "channel" not in parts:
        return None
    idx = parts.index("channel")
    if idx + 1 >= len(parts):
        return None
    candidate = parts[idx + 1]
    return candidate if CHANNEL_ID_RE.fullmatch(candidate) else None


def _resolve_via_page(url: str):
    """Scrape the channel page HTML for its UC id and name. Cheap and fast."""
    try:
        r = requests.get(
            url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        r.raise_for_status()
        html = r.text
    except requests.RequestException:
        return None, None, None

    # YouTube channel pages often embed many unrelated channelId values for
    # recommendations. externalId is the canonical UC id for the page itself.
    m = re.search(r'"externalId":"(UC[\w-]{22})"', html)
    if not m:
        m = re.search(r'"channelId":"(UC[\w-]{22})"', html)
    cid = m.group(1) if m else None

    name = None
    nm = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if nm:
        name = nm.group(1)
    return cid, name, _channel_icon_from_html(html)


def _resolve_via_ytdlp(url: str):
    opts = _ytdlp_opts(
        {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
        }
    )
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except Exception:
        return None, None, None
    cid = info.get("channel_id") or info.get("uploader_id") or info.get("id")
    name = info.get("channel") or info.get("uploader") or info.get("title")
    if cid and not cid.startswith("UC"):
        cid = None
    return cid, name, None


def _resolve_via_search(raw: str, exact_only=False):
    query, expected_handle = _channel_search_query(raw)
    if not query:
        return None, None, None
    if exact_only and not expected_handle:
        return None, None, None

    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    try:
        r = requests.get(
            url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        r.raise_for_status()
    except requests.RequestException:
        return None, None, None

    candidates = _channel_search_candidates(r.text)
    if not candidates:
        return None, None, None

    if expected_handle:
        for candidate in candidates:
            handles = {candidate.get("handle"), candidate.get("canonical_handle")}
            if expected_handle in handles:
                return candidate["id"], candidate.get("name"), candidate.get("icon")
        return None, None, None

    candidate = candidates[0]
    return candidate["id"], candidate.get("name"), candidate.get("icon")


def _channel_search_query(raw: str):
    raw = raw.strip()
    if raw.startswith("http"):
        try:
            parsed = urlparse(raw)
        except ValueError:
            return None, None
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        parts = [
            part
            for part in parts
            if part not in {"about", "featured", "videos"}
        ]
        if not parts:
            return None, None
        part = parts[-1]
        if part.startswith("@"):
            return part[1:], part.lower()
        return part, None

    if raw.startswith("@"):
        return raw[1:], raw.lower()

    if _looks_like_handle(raw):
        return raw, f"@{raw.lower()}"

    return raw, None


def _looks_like_handle(value: str):
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{3,30}", value))


def _channel_search_candidates(html: str):
    candidates = []
    for match in re.finditer(r'"channelRenderer":\{', html):
        block = html[match.start():match.start() + 6000]
        cid_match = re.search(r'"channelId":"(UC[\w-]{22})"', block)
        if not cid_match:
            continue

        name = _json_string_from_match(
            re.search(r'"title":\{"simpleText":"((?:\\.|[^"\\])*)"', block)
        )
        handle = _json_string_from_match(
            re.search(
                r'"subscriberCountText":\{"simpleText":"(@(?:\\.|[^"\\])*)"',
                block,
            )
        )
        canonical = _json_string_from_match(
            re.search(r'"canonicalBaseUrl":"(/@(?:\\.|[^"\\])*)"', block)
        )
        icon = _json_string_from_match(
            re.search(
                r'"thumbnail":\{"thumbnails":\[\{"url":"((?:\\.|[^"\\])*)"',
                block,
            )
        )
        canonical_handle = canonical[1:].lower() if canonical else None
        candidates.append(
            {
                "id": cid_match.group(1),
                "name": name,
                "icon": _normalize_icon_url(icon),
                "handle": handle.lower() if handle else None,
                "canonical_handle": canonical_handle,
            }
        )
    return candidates


def _json_string_from_match(match):
    if not match:
        return None
    value = match.group(1)
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def _channel_icon(cid: str, name: str | None = None):
    page_cid, _page_name, icon = _resolve_via_page(
        f"https://www.youtube.com/channel/{cid}"
    )
    if page_cid == cid and icon:
        return icon

    if name and name != cid:
        search_cid, _search_name, icon = _resolve_via_search(name)
        if search_cid == cid and icon:
            return icon
    return None


def _channel_icon_from_html(html: str):
    for pattern in (
        r'<meta property="og:image" content="([^"]+)"',
        r'<meta content="([^"]+)" property="og:image"',
    ):
        match = re.search(pattern, html)
        if match:
            return _normalize_icon_url(match.group(1))
    return None


def _normalize_icon_url(url: str | None):
    if not url:
        return None
    url = html_unescape(url).strip()
    if url.startswith("//"):
        url = f"https:{url}"
    return url if url.startswith(("http://", "https://")) else None


def _channel_icon_for_api(url: str | None):
    url = _normalize_icon_url(url)
    if not url:
        return None

    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("googleusercontent.com"):
        return re.sub(r"=s\d+", f"=s{CHANNEL_ICON_SIZE}", url, count=1)
    return url


def _channel_name_from_rss(cid: str):
    try:
        r = requests.get(
            YT_RSS.format(cid=cid), timeout=HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        el = root.find(f"{_ATOM}author/{_ATOM}name")
        return el.text if el is not None else None
    except (requests.RequestException, ET.ParseError):
        return None


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def require_admin(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != ADMIN_USER or auth.password != ADMIN_PASSWORD:
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Roman\'s Tube admin"'},
            )
        return fn(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------
# Routes — API (consumed by the tvOS app)
# --------------------------------------------------------------------------

@app.get("/health")
def health():
    profiles = load_profiles()
    return jsonify(
        {
            "ok": True,
            "version": APP_VERSION,
            "profiles": len(profiles),
            "channels": sum(len(profile.get("channels", [])) for profile in profiles),
            "ytDlp": ytdlp_version.__version__,
            "ytDlpEjs": _package_version("yt-dlp-ejs"),
            "jsRuntimes": YTDLP_JS_RUNTIMES,
            "remoteComponents": YTDLP_REMOTE_COMPONENTS,
            "tools": {
                "ffmpeg": _tool_version("ffmpeg"),
                "ffprobe": _tool_version("ffprobe"),
                "node": _tool_version("node"),
                "deno": _tool_version("deno"),
            },
        }
    )


@app.get("/api/profiles")
def api_profiles():
    profiles = sorted(load_profiles(), key=lambda profile: profile["name"].lower())
    return jsonify({"profiles": [_profile_for_api(profile) for profile in profiles]})


@app.get("/api/feed")
def api_feed():
    force = request.args.get("refresh") == "1"
    return jsonify({"videos": get_feed(force=force)})


@app.get("/api/profiles/<profile_id>/feed")
def api_profile_feed(profile_id):
    if get_profile(profile_id) is None:
        return jsonify({"error": "profile_not_found"}), 404
    force = request.args.get("refresh") == "1"
    return jsonify({"videos": get_feed(profile_id, force=force)})


@app.get("/api/channels")
def api_channels():
    return _channels_response(LEGACY_PROFILE_ID)


@app.get("/api/profiles/<profile_id>/channels")
def api_profile_channels(profile_id):
    if get_profile(profile_id) is None:
        return jsonify({"error": "profile_not_found"}), 404
    return _channels_response(profile_id)


def _channels_response(profile_id):
    channels = [
        {
            "id": ch["id"],
            "name": ch.get("name") or ch["id"],
            "icon": _channel_icon_for_api(ch.get("icon")),
        }
        for ch in load_channels(profile_id)
        if ch.get("id")
    ]
    channels.sort(key=lambda ch: ch["name"].lower())
    return jsonify({"channels": channels})


@app.get("/api/channels/<channel_id>/videos")
def api_channel_videos(channel_id):
    return _channel_videos_response(LEGACY_PROFILE_ID, channel_id)


@app.get("/api/profiles/<profile_id>/channels/<channel_id>/videos")
def api_profile_channel_videos(profile_id, channel_id):
    if get_profile(profile_id) is None:
        return jsonify({"error": "profile_not_found"}), 404
    return _channel_videos_response(profile_id, channel_id)


def _channel_videos_response(profile_id, channel_id):
    channels = load_channels(profile_id)
    channel = next((ch for ch in channels if ch["id"] == channel_id), None)
    if channel is None:
        return jsonify({"error": "channel_not_found"}), 404

    try:
        offset = max(0, int(request.args.get("offset", "0")))
        limit = min(50, max(1, int(request.args.get("limit", "24"))))
    except ValueError:
        return jsonify({"error": "bad_pagination"}), 400

    videos, has_more = fetch_channel_videos_page(channel, offset=offset, limit=limit)
    return jsonify({"videos": videos, "hasMore": has_more, "nextOffset": offset + limit})


@app.get("/api/stream/<video_id>")
def api_stream(video_id):
    # Safety net: never hand back a Short, even if one slipped through.
    if is_short(video_id):
        return jsonify({"error": "shorts_not_allowed"}), 403
    try:
        out_dir = start_hls(video_id)
    except Exception as exc:  # yt-dlp / ffmpeg can throw a variety of errors
        error, status, detail = _classify_stream_error(exc)
        if status in (403, 410, 451):
            _remember_playability(video_id, False, error)
        app.logger.warning("stream failed for %s: %s", video_id, detail)
        return jsonify({"error": error, "detail": detail}), status

    # Wait for ffmpeg to write the first playlist before telling the app to play.
    index = out_dir / "index.m3u8"
    deadline = time.time() + INDEX_WAIT_SECONDS
    while not index.exists() and time.time() < deadline:
        time.sleep(0.3)
    if not index.exists():
        return jsonify({"error": "hls_start_timeout"}), 504

    stream_url = request.host_url.rstrip("/") + f"/hls/{video_id}/index.m3u8"
    return jsonify({"id": video_id, "url": stream_url, "type": "hls"})


@app.get("/hls/<video_id>/<path:filename>")
def hls_file(video_id, filename):
    """Serve the HLS playlist and segments produced by ffmpeg."""
    safe = os.path.basename(filename)  # block path traversal
    path = HLS_ROOT / video_id / safe

    # A segment the player asked for may be a beat behind ffmpeg — wait briefly.
    if not path.exists():
        deadline = time.time() + 10
        while not path.exists() and time.time() < deadline:
            time.sleep(0.2)
    if not path.exists():
        return jsonify({"error": "not_found"}), 404

    with _hls_lock:
        sess = _hls_sessions.get(video_id)
        if sess:
            sess["last"] = time.time()

    mimetype = (
        "application/vnd.apple.mpegurl" if safe.endswith(".m3u8") else "video/mp2t"
    )
    return send_file(path, mimetype=mimetype, conditional=True)


@app.get("/profile_pictures/<path:filename>")
def profile_picture_file(filename):
    safe = os.path.basename(filename)
    path = PROFILE_PICTURES_DIR / safe
    if not path.exists():
        return jsonify({"error": "not_found"}), 404
    return send_file(path, conditional=True)


# --------------------------------------------------------------------------
# Routes — Web admin
# --------------------------------------------------------------------------

@app.get("/admin")
@require_admin
def admin():
    return _render_admin()


@app.post("/admin/profiles")
@require_admin
def admin_create_profile():
    try:
        profile = create_profile(request.form.get("name", ""))
    except ValueError as exc:
        return _render_admin(error=str(exc))
    return redirect(url_for("admin_profile", profile_id=profile["id"]))


@app.get("/admin/<profile_id>")
@require_admin
def admin_profile(profile_id):
    if get_profile(profile_id) is None:
        return _render_admin(error="Profile not found"), 404
    return _render_admin(profile_id=profile_id)


@app.post("/admin/<profile_id>/name")
@require_admin
def admin_rename_profile(profile_id):
    try:
        rename_profile(profile_id, request.form.get("name", ""))
    except ValueError as exc:
        status = 404 if str(exc) == "Profile not found" else 400
        return _render_admin(profile_id=profile_id, error=str(exc)), status
    return redirect(url_for("admin_profile", profile_id=profile_id))


@app.post("/admin/<profile_id>/add")
@require_admin
def admin_add(profile_id):
    if get_profile(profile_id) is None:
        return _render_admin(error="Profile not found"), 404
    raw = request.form.get("channel", "")
    try:
        ch = resolve_channel(raw)
    except ValueError as exc:
        return _render_admin(profile_id=profile_id, error=str(exc), value=raw)
    channels = load_channels(profile_id)
    if not any(c["id"] == ch["id"] for c in channels):
        channels.append(ch)
        save_channels(channels, profile_id)
        get_feed(profile_id, force=True)  # rebuild so the new channel shows up immediately
    return redirect(url_for("admin_profile", profile_id=profile_id))


@app.post("/admin/<profile_id>/delete")
@require_admin
def admin_delete(profile_id):
    if get_profile(profile_id) is None:
        return _render_admin(error="Profile not found"), 404
    cid = request.form.get("id", "")
    channels = [c for c in load_channels(profile_id) if c["id"] != cid]
    save_channels(channels, profile_id)
    get_feed(profile_id, force=True)
    return redirect(url_for("admin_profile", profile_id=profile_id))


@app.post("/admin/<profile_id>/picture")
@require_admin
def admin_set_picture(profile_id):
    profile = get_profile(profile_id)
    if profile is None:
        return _render_admin(error="Profile not found"), 404

    if request.form.get("clear_picture") == "1":
        profile["picture"] = ""
        save_profile(profile)
        return redirect(url_for("admin_profile", profile_id=profile_id))

    picture_url = request.form.get("picture_url", "").strip()
    upload = request.files.get("picture")
    if upload and upload.filename:
        ext = _uploaded_picture_extension(upload)
        if ext is None:
            return _render_admin(
                profile_id=profile_id,
                error="Profile picture must be a PNG, JPEG, GIF, or WebP image.",
            )
        filename = f"{profile_id}{ext}"
        upload.save(PROFILE_PICTURES_DIR / filename)
        profile["picture"] = url_for("profile_picture_file", filename=filename)
        save_profile(profile)
    elif picture_url:
        if not _normalize_icon_url(picture_url):
            return _render_admin(profile_id=profile_id, error="Profile picture URL must start with http:// or https://.")
        profile["picture"] = picture_url
        save_profile(profile)

    return redirect(url_for("admin_profile", profile_id=profile_id))


def _uploaded_picture_extension(upload):
    content_type = (upload.mimetype or "").lower()
    if content_type == "image/png":
        return ".png"
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if content_type == "image/gif":
        return ".gif"
    if content_type == "image/webp":
        return ".webp"
    suffix = Path(upload.filename or "").suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else None


def _admin_profiles():
    profiles = load_profiles()
    profiles.sort(key=lambda profile: profile["name"].lower())
    return [
        {
            **profile,
            "picture_url": _profile_picture_for_api(profile),
            "channel_count": len(profile.get("channels", [])),
        }
        for profile in profiles
    ]


def _render_admin(profile_id: str | None = None, **kwargs):
    selected_profile = get_profile(profile_id) if profile_id else None
    channels = admin_channels(profile_id) if selected_profile else []
    return render_template(
        "admin.html",
        profiles=_admin_profiles(),
        selected_profile={
            **selected_profile,
            "picture_url": _profile_picture_for_api(selected_profile),
        } if selected_profile else None,
        channels=channels,
        **kwargs,
    )


if __name__ == "__main__":
    # 0.0.0.0 so the Apple TV on the LAN can reach it. Port 8000.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
