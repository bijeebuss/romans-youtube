# Roman's Tube

A private "YouTube for my kid" for the Apple TV. It shows a single
**chronological feed** built only from **channels you approve**, and plays them
with the native Apple TV player — no recommendations, no autoplay rabbit holes,
no Shorts, no ads, no account.

Built for personal/family use, not for the App Store.

## How it works

```
   ┌─────────────────┐                ┌───────────────────────────┐       ┌───────────┐
   │  Apple TV app    │   feed JSON    │   Home server (Ryzen box)  │  RSS  │  YouTube   │
   │  (SwiftUI/tvOS)  │ ◀───────────── │   Flask + yt-dlp + ffmpeg  │ ◀──── │            │
   │                  │                │                            │       │            │
   │  • feed grid     │ ── play ─────▶ │  • merges channel RSS       │       │            │
   │  • AVPlayer      │                │  • filters out Shorts       │       │            │
   │                  │  ◀── HLS ───── │  • yt-dlp picks ≤1080p ◀─────┼───────│ googlevideo│
   │                  │   (1080p)      │  • ffmpeg muxes → HLS  ◀─────┼── av  └───────────┘
   └─────────────────┘                │  • /admin whitelist page    │  streams in
                                       └───────────────────────────┘
```

**The whole video is never downloaded or stored.** Per video the server does
two jobs: it lists what's new (YouTube RSS), and when your kid picks something
it asks `yt-dlp` for the best video+audio (≤1080p) and runs `ffmpeg` to mux them
into a live **HLS** stream the Apple TV plays.

Because 1080p comes as *separate* video and audio tracks (and AVPlayer can't mux
those itself), the playback now flows **through** the server: `ffmpeg` pulls from
YouTube and produces HLS segments on the fly. Those segments are transient — held
briefly on disk, served to the Apple TV, then reaped — so there's still no large
library sitting on the box and no wait for a full download. (Set `MAX_HEIGHT=720`
to make the server copy-only with the lightest possible load.)

Two pieces, because tvOS has no web browser engine and can't run `yt-dlp` or
`ffmpeg` itself:

- [`server/`](server/) — runs on your always-on home server (the Ryzen 7500
  box), on the **same network** as the Apple TV. Dockerized.
- [`tvos/`](tvos/) — the SwiftUI Apple TV app.

## Quick start

1. **Server** ([details](server/README.md)):
   ```bash
   cd server
   # edit docker-compose.yml → change ADMIN_PASSWORD
   docker compose up -d --build
   ```
   Open `http://SERVER_IP:8000/admin`, log in, and add approved channels.
   Then confirm playback works before setting up the TV:
   ```bash
   docker compose exec romanstube python3 selftest.py
   ```

2. **App** ([details](tvos/README.md)):
   ```bash
   cd tvos
   brew install xcodegen && xcodegen generate
   open RomansTube.xcodeproj
   ```
   Set your signing team, run it on the Apple TV, and enter the server's address
   (`SERVER_IP:8000`) on the app's Settings screen.

## Why same-network?

YouTube's stream URLs are tied to the IP that requested them. `ffmpeg` pulling on
the home server and the Apple TV playing on the same LAN works because they share
your home's public IP. A cloud-hosted server would get 403'd by googlevideo.

## Good to know / limits

- **Quality**: up to **1080p**. The server muxes YouTube's separate video+audio
  into HLS with ffmpeg — copying H.264 when available, transcoding VP9/AV1 to
  H.264 otherwise (easy work for a desktop CPU like a Ryzen 7500). Set
  `MAX_HEIGHT=720` in the compose file to lighten the load.
- **Keep yt-dlp fresh**: YouTube changes things; rebuild the container
  occasionally (`docker compose build --no-cache && docker compose up -d`).
- **Not hardened for a curious teenager**: the app's Settings screen (server
  address) has no PIN, and the feed is whatever those channels post. Possible
  next steps if you want them: a PIN gate on Settings, per-video approval queue
  in the admin page, or a daily watch-time limit.
- **Respect YouTube's ToS**: this is a personal tool for your own viewing.
# romans-youtube
