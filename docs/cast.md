# Casting to TVs and Chromecasts

Pluteus can play music and video from the library on a TV or Chromecast on your own network. Off by default. Status: **the server side and the
web API are in; there is no button in the web pages yet** (that is the next step), so for now you start playback with the API (examples below).

Supported: **Google Cast** (Chromecast, Google TV and Android TV devices with Chromecast built in). DLNA/UPnP is planned next. Not planned: AirPlay,
Roku, Miracast.

## How it works

Pluteus does not send the device the file. It sends the device the *address* of a signed link, and the device fetches the file from Pluteus itself:

```
browser / app ──► Pluteus ──(play this address)──► TV
                     ▲                              │
                     └──── the TV fetches the file ──┘   /cast/<token>/<name>   (signed, one file, one user, 12 hours)
```

So two things must be true: Pluteus can reach the TV, and the TV can reach Pluteus.

## Set it up

**1. Find the TV's address.** On the TV (Settings, Network) or in your router's device list. Give it a fixed address in the router (a DHCP
reservation) so it does not change. TVs with Chromecast built in, such as Google TV models, listen on port 8009; nothing to switch on.

**2. Install the one library the Google Cast support needs**, where Pluteus runs. In Docker, add it to the image (the `Dockerfile` line
`RUN pip install --no-cache-dir .` becomes `RUN pip install --no-cache-dir ".[cast]"`) and rebuild; natively, `pip install pychromecast`.

**3. Add the TV to `config.toml`** and switch casting on:

```toml
[server]
cast = true
cast_media_base = "http://192.168.1.10:8100"    # how the TV reaches Pluteus: this computer's network address and the published port

[[cast_devices]]
name = "Living room"
address = "192.168.1.50"
# users = ["alice"]      # who may cast here; leave out for every signed-in user
```

- **`cast_media_base`** matters most with Docker Desktop on Windows. Without it Pluteus uses the address the page was opened with, which is
  useless to a TV when you opened it as `localhost` (Pluteus then refuses with a message saying so). Use this computer's address on the network
  and the port Docker publishes (`8100` in the example compose file). Not your public hostname: from inside your own network it often does not
  resolve, or does not loop back.
- **Windows Firewall** may block the TV from reaching that port. If a play command is accepted but nothing happens, check that first.
- Devices are listed by address on purpose: automatic discovery (mDNS, SSDP) does not pass through Docker Desktop on Windows.

**4. Restart Pluteus.** The admin page, Status, Services, shows "Casting" and how many devices are connected. Pluteus connects to each device
in the background and keeps trying every 30 seconds, so a TV that is off when Pluteus starts is picked up when it is switched on.

**5. Try it.** As a signed-in user (the examples use Basic login; a browser session works the same way, with the site's own `Origin` on writes):

```bash
curl -u alice:PASSWORD http://192.168.1.10:8100/api/cast
curl -u alice:PASSWORD -H "Origin: http://192.168.1.10:8100" -H "Content-Type: application/json" \
     -d '{"device":"Living room","root":"music","path":"Artist/Album/01 Song.mp3"}' http://192.168.1.10:8100/api/cast/play
curl -u alice:PASSWORD -H "Origin: http://192.168.1.10:8100" -H "Content-Type: application/json" \
     -d '{"device":"Living room","op":"pause"}' http://192.168.1.10:8100/api/cast/control
```

`op` is `pause`, `resume`, `stop`, `seek` (with `value` in seconds) or `volume` (with `value` 0 to 100). `/api/cast` lists the devices with what each is
doing (`state`, `position`, `duration`, `volume`, `title`) and, for one that does not answer, `error` says why.

## What plays

A Chromecast plays these as they are: MP3, AAC and M4A, FLAC, Ogg and Opus, WAV; MP4 (H.264) and WebM video. Other types (MKV, MOV, HEVC video) are
refused with a sentence saying so. Converting them with ffmpeg, as an optional feature, is a later step.

## Access

- Any signed-in user may cast; `users = [...]` on a device narrows it. The shared anonymous user cannot.
- A media address opens one file, for one user, for twelve hours, and is checked again with that user's current rights every time. See SECURITY.md,
  "Casting".

## Not there yet

| Step | What |
|---|---|
| next | "Play on…" in the player bar and on folder pages, the queue, Now Playing in the page |
| then | DLNA/UPnP devices (many TVs, receivers and speakers) |
| later | optional ffmpeg conversion for files a device cannot play; a way to find devices on the network instead of typing addresses |

Not tried: against a real Chromecast or TV. The pychromecast calls were checked against the library's own signatures (version 14); everything else runs
against a stand-in for the library, and the media address was fetched, Range requests included, over real HTTP.
