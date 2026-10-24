# Casting to Chromecasts

Pluteus can play music and video from the library on a Chromecast (the first and second generation, the round one, and newer). Status:
**step C1 of the plan**: the server side, the agent and the web API are in; there is no button in the web pages yet (that is C2), so for now
you start playback with the API (examples below). Off by default.

## How it fits together

A Chromecast cannot log in, cannot join a tailnet, and fetches its media itself, so pluteus does not talk to it. A small separate program, the
**cast agent**, runs on the same network as the Chromecasts and only ever connects *out* to pluteus:

```
browser / app ──► pluteus server ◄── long poll (outbound only) ── cast agent ──► Chromecast
                      │                                              ▲  │
                      └── signed media address ── fetched by ────────┘  └── plays the agent's own address
```

- The agent reports which Chromecasts it sees and what each is doing (`POST /cast-agent/status`).
- It asks for commands with a long poll (`GET /cast-agent/poll`): play, pause, resume, stop, seek, volume.
- For "play" pluteus sends a **signed address** that names one file for one user (twelve hours). The agent fetches the file from pluteus and
  serves it to the Chromecast from its own small web server, so the Chromecast never has to reach pluteus. That is what makes a site over a
  tailnet, or behind someone else's router, work: only the agent needs a route to pluteus.
- The same agent works at home. Run it on the Docker host or a Pi, not inside the container: Chromecast discovery (mDNS) does not pass through
  Docker Desktop on Windows, which is also why you can give the Chromecasts' addresses with `--host`.

## Set it up

**1. On the pluteus server**: make a token for the site and add the site to `config.toml`.

```powershell
python -m pluteus cast-token --name Cabin
```

It prints the token (shown only once) and the two lines to put in the config. Set `cast = true` under `[server]`, add the lines as a
`[[cast_sites]]` entry (see `config.example.toml`), restart. The admin page, Status, Services, shows how many sites are online.

**2. On a machine at the site** (a Raspberry Pi 4 or any small always-on computer with Python 3.11 or newer):

```bash
pip install pychromecast
# copy pluteus/castagent.py here (it is one file, nothing else of pluteus is needed)
echo 'the-token-from-step-1' > agent.token
chmod 600 agent.token
python3 castagent.py --server https://pluteus.example.org --token-file agent.token --host 192.168.1.50
```

- `--server` is any address the agent can reach pluteus at: the public address, or a tailnet address (`http://pluteus-server:8080`). A base path
  is fine (`https://example.org/pluteus`).
- `--host` is a Chromecast's address; repeat it for more. Without `--host` the agent searches the network (mDNS), which needs the Chromecasts
  on the same network segment.
- `--advertise-host` is this machine's address as the Chromecasts see it (found automatically; set it when the machine has several).
- `--listen-port` is the port of the agent's web server (default 8765); the Chromecasts must be able to reach it.
- The agent looks for Chromecasts once, at start. Restart it after adding one.

To keep it running on a Pi, a systemd unit (`/etc/systemd/system/castagent.service`):

```ini
[Unit]
Description=pluteus cast agent
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/castagent
ExecStart=/usr/bin/python3 castagent.py --server https://pluteus.example.org --token-file agent.token --host 192.168.1.50
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**3. Try it.** With the agent running, as a signed-in user (the examples use Basic login; a browser session works the same way, with the
site's own `Origin` on the writes):

```bash
curl -u alice:PASSWORD https://pluteus.example.org/api/cast
curl -u alice:PASSWORD -H "Origin: https://pluteus.example.org" -H "Content-Type: application/json" \
     -d '{"site":"Cabin","device":"<id from the list>","root":"music","path":"Artist/Album/01 Song.mp3"}' \
     https://pluteus.example.org/api/cast/play
curl -u alice:PASSWORD -H "Origin: https://pluteus.example.org" -H "Content-Type: application/json" \
     -d '{"site":"Cabin","device":"<id>","op":"pause"}' https://pluteus.example.org/api/cast/control
```

`op` is `pause`, `resume`, `stop`, `seek` (with `value` in seconds) or `volume` (with `value` 0 to 100).

## What plays

A Chromecast plays these as they are: MP3, AAC and M4A, FLAC, Ogg and Opus, WAV; MP4 (H.264) and WebM video. Other types (MKV, MOV, HEVC
video) are refused with a sentence saying so. **Converting them with ffmpeg, as an optional feature, is step C3.**

## Access

- Any signed-in user may cast; `users = [...]` in a `[[cast_sites]]` entry narrows a site to some of them. The shared anonymous user cannot.
- The agent's token opens only the two agent addresses. A media address opens one file, for one user, for twelve hours, and is checked again
  with that user's current rights every time. See SECURITY.md, "Casting to Chromecasts".

## What is not there yet

| Step | What |
|---|---|
| C2 | "Play on…" in the player bar and on folder pages, the queue, Now Playing in the page |
| C3 | the agent caches ahead of the playhead; a bitrate cap per site; ffmpeg conversion of files a Chromecast cannot play |
| C4 | an admin view of sites and agents; discovering Chromecasts that appear after the agent started; a Pi setup guide with Tailscale |
| C5 | optional custom receiver with cover art and notes |

Not tried: the agent against a real Chromecast. The pychromecast calls were checked against the library's own signatures (version 14) and the
rest of the chain against a stand-in, over real HTTP.
