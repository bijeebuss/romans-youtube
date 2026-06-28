# Roman's Tube — Apple TV app

A SwiftUI tvOS app that shows the chronological feed from your home server and
plays videos with the native Apple TV player. No YouTube account, no ads, no
recommendations — only the channels you approved.

## Build & install (you'll do this on your Mac)

You have "some experience", so here are the concrete steps.

### 1. Generate the Xcode project

The project is defined in `project.yml` (via [XcodeGen]) so it's clean and
diff-friendly. Generate the actual `.xcodeproj`:

```bash
brew install xcodegen          # one-time
cd tvos
xcodegen generate
open RomansTube.xcodeproj
```

[XcodeGen]: https://github.com/yonsei/XcodeGen

> Prefer not to use XcodeGen? Instead create a new **tvOS App** in Xcode
> (SwiftUI lifecycle), delete its starter files, and drag everything in
> `Sources/` into the target. Then skip to step 3.

### 2. Set your signing team

In Xcode: select the **RomansTube** target → **Signing & Capabilities** →
check **Automatically manage signing** → pick your Apple ID team.

- A **free** Apple ID works — the app just needs re-signing every 7 days.
- A **paid** Developer account ($99/yr) lasts a year between re-signs.

(You can also set `DEVELOPMENT_TEAM` in `project.yml` and re-run `xcodegen`.)

### 3. Run it on the Apple TV

1. Put the Apple TV and your Mac on the same network.
2. **Settings → Remotes and Devices → Remote App and Devices** on the Apple TV
   (this puts it in pairing mode).
3. In Xcode: **Window → Devices and Simulators → Discovered**, find the Apple
   TV, and pair it (enter the code shown on the TV).
4. Pick the Apple TV as the run destination (top of the Xcode window) and press
   **▶ Run**. First install takes a minute.

You can also test in the **tvOS Simulator** first (no pairing needed), though
the simulator can't always reach a LAN server — the real device is the true
test.

### 4. First launch

The app opens to **Settings**. Enter your server address, e.g. `192.168.1.50:8000`
(the Pi's IP and port), tap **Test Connection**, then **Save**. The feed loads.

## App icon

`Resources/Assets.xcassets` contains the required tvOS "App Icon & Top Shelf
Image" brand asset. The icon and top-shelf wells are populated from
`icon.png` with the required 1x and 2x renditions.

## How it behaves

- **Feed**: newest approved videos first. Pull-to-refresh, or it refreshes when
  you change settings.
- **Play**: selecting a card asks the server for a stream URL (a brief spinner
  while yt-dlp resolves it) then plays full-screen with the native transport.
- **Shorts**: never appear — filtered server-side.
- **Parental gate**: the Settings screen (server address) isn't behind a PIN
  yet — see the top-level README for hardening ideas if your kid is old enough
  to go poking.
