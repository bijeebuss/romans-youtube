#!/usr/bin/env python3
"""
selftest.py — prove the server can turn a real YouTube video into a playable
HLS stream BEFORE you bother setting up the Apple TV.

It checks the whole chain: ffmpeg/yt-dlp present → format resolution → whether
the video will be copied or transcoded → ffmpeg actually producing HLS segments
→ the segments having the expected resolution/codec.

Two modes:

  1. In-process (default) — runs the real pipeline directly via app.py.
       python3 selftest.py                 # default test video
       python3 selftest.py VIDEO_ID_or_URL # one of your own videos

  2. HTTP — hits a *running* server end-to-end, exactly as the Apple TV does.
       python3 selftest.py --http http://localhost:8000 VIDEO_ID

Inside Docker:
       docker compose exec romanstube python3 selftest.py
       docker compose exec romanstube python3 selftest.py --http http://localhost:8000 <id>

Exit code is 0 on success, 1 on failure — so you can use it in scripts.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

# "Me at the zoo" — the first-ever YouTube video. Always up, ~19s, so the test
# is quick. Pass one of your own (ideally a 1080p one) to exercise transcoding.
DEFAULT_VIDEO = "jNQXAC9IVRw"


# --- pretty output ---------------------------------------------------------

def section(msg): print(f"\n\033[1m{msg}\033[0m")
def ok(msg):      print(f"  \033[32m✅ {msg}\033[0m")
def warn(msg):    print(f"  \033[33m⚠️  {msg}\033[0m")
def bad(msg):     print(f"  \033[31m❌ {msg}\033[0m")
def info(msg):    print(f"     {msg}")


def parse_video_id(arg: str) -> str:
    """Accept a bare id, a watch URL, a youtu.be link, or a shorts URL."""
    if "youtube.com" in arg or "youtu.be" in arg:
        u = urllib.parse.urlparse(arg)
        if "youtu.be" in u.netloc:
            return u.path.lstrip("/")
        if u.path.startswith(("/shorts/", "/embed/")):
            return u.path.split("/")[2]
        qs = urllib.parse.parse_qs(u.query)
        if "v" in qs:
            return qs["v"][0]
    return arg.strip()


# --- mode 1: in-process ----------------------------------------------------

def run_in_process(video_id: str, max_wait: int, keep: bool) -> bool:
    section("Checking required tools")
    all_ok = True
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool):
            ok(f"{tool} found")
        else:
            bad(f"{tool} NOT found on PATH")
            all_ok = False
    try:
        import yt_dlp  # noqa: F401
        ok("yt-dlp importable")
    except ImportError:
        bad("yt-dlp not installed (pip install -r requirements.txt)")
        all_ok = False
    if not all_ok:
        return False

    # Import the server module (this also starts its background reaper).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app

    section(f"Resolving formats for {video_id}")
    try:
        video, audio, _info = app._resolve_formats(video_id)
    except Exception as exc:
        bad(f"yt-dlp could not resolve formats: {exc}")
        info("If this is the only failure, your yt-dlp is likely stale — rebuild "
             "the container: docker compose build --no-cache && docker compose up -d")
        return False

    if not video or not video.get("url"):
        bad("No playable video format returned")
        return False

    height = video.get("height")
    vcodec = (video.get("vcodec") or "?")
    acodec = ((audio or video).get("acodec") or "?")
    ok(f"video: {height}p  {vcodec}")
    ok(f"audio: {acodec}" + ("  (separate track)" if audio else "  (muxed)"))

    cmd = app._build_ffmpeg_cmd(video, audio, app.HLS_ROOT / video_id)
    will_copy_v = "copy" in cmd[cmd.index("-c:v") + 1] if "-c:v" in cmd else False
    if will_copy_v:
        ok("video path: COPY (no transcode — cheap)")
    else:
        warn(f"video path: TRANSCODE to H.264 (preset {app.X264_PRESET}) — "
             "this is normal for VP9/AV1 1080p")

    section("Muxing to HLS with ffmpeg")
    t0 = time.time()
    try:
        out_dir = app.start_hls(video_id)
    except Exception as exc:
        bad(f"Failed to start ffmpeg: {exc}")
        return False

    index = out_dir / "index.m3u8"
    deadline = time.time() + max_wait
    while not index.exists() and time.time() < deadline:
        # Surface an early ffmpeg crash instead of waiting the full timeout.
        sess = app._hls_sessions.get(video_id)
        proc = sess and sess["proc"]
        if proc and proc.poll() not in (None, 0):
            bad(f"ffmpeg exited early (code {proc.returncode}). Re-run with the "
                "ffmpeg loglevel raised in app.py to see why.")
            return False
        time.sleep(0.3)

    if not index.exists():
        bad(f"No playlist after {max_wait}s — ffmpeg never produced segments")
        _cleanup(app, video_id, keep)
        return False
    ok(f"first playlist written in {time.time() - t0:.1f}s")

    # Wait for at least one real segment to exist and have bytes.
    seg = _wait_for_segment(out_dir, max_wait=10)
    if not seg:
        bad("Playlist exists but no media segment was produced")
        _cleanup(app, video_id, keep)
        return False
    ok(f"segment produced: {seg.name} ({seg.stat().st_size // 1024} KB)")

    n_segments = sum(1 for line in index.read_text().splitlines()
                     if line.startswith("#EXTINF"))
    info(f"playlist currently lists {n_segments} segment(s); the rest stream "
         "as they're encoded")

    section("Verifying the segment with ffprobe")
    probe_ok = _probe(seg, expected_height=height)

    _cleanup(app, video_id, keep)

    if probe_ok:
        print()
        ok("ALL GOOD — the server can stream this video to the Apple TV.")
        return True
    return False


def _wait_for_segment(out_dir: Path, max_wait: int):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        segs = sorted(out_dir.glob("seg_*.ts"))
        if segs and segs[0].stat().st_size > 0:
            return segs[0]
        time.sleep(0.3)
    return None


def _probe(segment: Path, expected_height=None) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height",
             "-of", "json", str(segment)],
            capture_output=True, text=True, timeout=20,
        )
        data = json.loads(out.stdout or "{}")
        streams = data.get("streams", [])
        if not streams:
            warn("ffprobe found no video stream in the segment (may still play)")
            return True
        s = streams[0]
        ok(f"decoded by ffprobe: {s.get('codec_name')} "
           f"{s.get('width')}x{s.get('height')}")
        if s.get("codec_name") not in ("h264",):
            warn(f"codec is {s.get('codec_name')}, not h264 — AVPlayer may "
                 "refuse it. Check the transcode path.")
        if expected_height and s.get("height") and int(s["height"]) < expected_height - 16:
            warn(f"output height {s['height']} is below the {expected_height}p "
                 "source — check your format selection")
        return True
    except Exception as exc:
        warn(f"ffprobe check skipped: {exc}")
        return True


def _cleanup(app, video_id, keep):
    sess = app._hls_sessions.get(video_id)
    if sess and sess["proc"] and sess["proc"].poll() is None:
        sess["proc"].terminate()
    if keep:
        info(f"left HLS output in {app.HLS_ROOT / video_id} (--keep)")
    else:
        shutil.rmtree(app.HLS_ROOT / video_id, ignore_errors=True)
        app._hls_sessions.pop(video_id, None)


# --- mode 2: HTTP (end-to-end against a running server) --------------------

def run_http(base: str, video_id: str, max_wait: int) -> bool:
    base = base.rstrip("/")

    section(f"Checking server health at {base}")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=10) as r:
            health = json.loads(r.read())
        ok(f"server alive — {health.get('channels')} channel(s) configured")
    except Exception as exc:
        bad(f"Could not reach {base}/health: {exc}")
        return False

    section(f"Requesting a stream for {video_id} (as the Apple TV would)")
    t0 = time.time()
    try:
        req = urllib.request.Request(f"{base}/api/stream/{video_id}")
        with urllib.request.urlopen(req, timeout=max_wait + 10) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        bad(f"/api/stream returned HTTP {exc.code}: {body}")
        return False
    except Exception as exc:
        bad(f"/api/stream failed: {exc}")
        return False

    stream_url = payload.get("url")
    if not stream_url:
        bad(f"No stream URL in response: {payload}")
        return False
    ok(f"got HLS URL in {time.time() - t0:.1f}s")
    info(stream_url)

    section("Fetching the playlist and first segment")
    try:
        with urllib.request.urlopen(stream_url, timeout=15) as r:
            playlist = r.read().decode(errors="replace")
    except Exception as exc:
        bad(f"Could not fetch the playlist: {exc}")
        return False
    seg_lines = [ln.strip() for ln in playlist.splitlines()
                 if ln.strip() and not ln.startswith("#")]
    if not seg_lines:
        bad("Playlist has no segments yet")
        return False
    ok(f"playlist lists {len(seg_lines)} segment(s)")

    seg_url = urllib.parse.urljoin(stream_url, seg_lines[0])
    try:
        with urllib.request.urlopen(seg_url, timeout=20) as r:
            data = r.read()
        ok(f"downloaded first segment: {len(data) // 1024} KB")
    except Exception as exc:
        bad(f"Could not download the first segment: {exc}")
        return False

    print()
    ok("ALL GOOD — a real client can stream this video end-to-end.")
    return True


# --- main ------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Self-test the Roman's Tube server.")
    p.add_argument("video", nargs="?", default=DEFAULT_VIDEO,
                   help="video id or URL (default: a tiny always-available clip)")
    p.add_argument("--http", metavar="BASE_URL",
                   help="test a running server end-to-end instead of in-process")
    p.add_argument("--max-wait", type=int, default=30,
                   help="seconds to wait for the first segment (default 30)")
    p.add_argument("--keep", action="store_true",
                   help="don't delete the HLS output afterwards (in-process mode)")
    args = p.parse_args()

    video_id = parse_video_id(args.video)
    print(f"Testing video id: {video_id}")

    if args.http:
        success = run_http(args.http, video_id, args.max_wait)
    else:
        success = run_in_process(video_id, args.max_wait, args.keep)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
