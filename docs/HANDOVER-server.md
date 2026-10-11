# Handover: bring pluteus up on the server

You are a Claude running on the Windows machine that hosts Docker (the "server"). The user developed pluteus on another
machine with another Claude and now wants it running here, in Docker, behind their existing Caddy. This file is everything you
need. Read it fully, then do the steps in order. Report after each numbered step; stop and ask when a step says so.

## What pluteus is

A small, filesystem-first server for a 6 TB book library and a 3.6 TB comic library: it reads the folders live from disk (no
index, no database of files), serves OPDS to Android reader apps (PocketBook), a web UI with the real folder tree, readers
for EPUB/CBZ/PDF, viewers for images, audio, video, text, Markdown, CSV and zip files, per-user logins, reading progress,
and a personal upload folder per user. Python, Starlette, SQLite for small state only. Repo: `Erlbon/pluteus` (private),
cloned here at `D:\Dev\pluteus`. Read `D:\Dev\pluteus\README.md` and `D:\Dev\pluteus\docs\running.md` for detail.

## What is already in place (done by the other Claude, over the network drive)

| Thing | Where | State |
|---|---|---|
| Code | `D:\Dev\pluteus` (git clone) | pulled to the latest commit; check `git log --oneline -1` against origin |
| Compose project | `D:\temp\_docker\pluteus\docker-compose.yml` | written, `docker compose config` validated, **never started here** |
| pluteus config | `D:\temp\_docker\pluteus\config\config.toml` | written; container paths; one **placeholder** user `admin` whose password nobody knows |
| Uploads folder | `D:\temp\_docker\pluteus\uploads\<user>\` | created on first use |
| Admin app | `D:\temp\_docker\pluteus\pluteus-admin.exe` | built and opens; edits `config.toml` (users, raw text); unsigned, SmartScreen may warn |
| Caddy | `D:\temp\_docker\caddy\caddyfile` | a `/pluteus` route was **added but Caddy was not reloaded**; backup: `caddyfile.bak-20261011` |
| Operator notes | `D:\temp\_docker\pluteus\HOWTO.md` | the same commands as below |

Compose facts: image built from `D:/Dev/pluteus`; container `pluteus` publishes **host port 8100 -> container 8080**; mounts
`D:/eLib/800 - Fiction` -> `/library/books` (read-only) and `E:/eLib/741.59 - Comic Books` -> `/library/comics` (read-only),
`./uploads` -> `/uploads`, `./config` -> `/config`, named volume `pluteus-data` -> `/data`. Hardened: read-only root
filesystem, `cap_drop: ALL`, `no-new-privileges`, 1 GB memory limit. pluteus serves under the URL prefix `/pluteus`
(`base_path`), and `trust_proxy = true` because Caddy is in front.

Caddy facts: container `caddy` (Docker Desktop), config `D:\temp\_docker\caddy\caddyfile`, other apps are reached as
`host.docker.internal:<port>`. The new block is `handle /pluteus/*` -> `host.docker.internal:8100` and a redirect from `/pluteus`.
Docker Desktop NAT makes every visitor look like one private address to Caddy; pluteus knows (its login throttle then
counts per user name, not per address).

## Rules (the user's, and they matter)

- **Do not touch the libraries.** They are mounted read-only on purpose. Never write, move or delete anything under
  `D:\eLib` or `E:\eLib`.
- **Do not change the router, firewall or port forwards.** Port 8100 must NOT be forwarded from the internet; only Caddy
  (443) is public. You may allow 8100 on the LAN if the Windows firewall blocks it.
- **Passwords: the user types them, you never do.** Never pick, print, log or store a real password, and never paste one
  into a command line you show. The `user set ... --password` command prompts the user; run it interactively or ask the user
  to run it.
- **Caddy: validate before reload, and keep the backup.** Only reload after `caddy validate` says valid. If anything else
  on the site stops working after the reload, restore `caddyfile.bak-20261011` over `caddyfile`, reload again, and tell the
  user. Do not edit the Caddyfile further without asking.
- Confirm with the user before anything hard to reverse or outward-facing (removing volumes, `docker compose down -v`,
  deleting files, changing other containers). Do not touch the other containers (Ubooquity, Jellyfin, Immich, ...).
- Scratch files go in `C:\Temp\Claude` only (one subfolder per task), nothing loose elsewhere; delete it when done.
- The user is on Windows PowerShell 5.1: no `&&`, no bash idioms in commands you give them. Use full absolute paths.
- Editing files that contain backslashes: use the Edit/Write tools, not heredocs or generated patch scripts (a `\n` in
  a script string has repeatedly become a real line break). Run the tests before any commit; never gate a commit or push on
  a piped test run.
- Do not commit server-specific files (the config, the compose file) into the repo; they hold real settings.

## Steps

### 1. Check the code and build

```powershell
cd D:\Dev\pluteus
git status -sb
git pull --ff-only
git log --oneline -1
cd D:\temp\_docker\pluteus
docker compose build
```

Expected: a clean tree, a successful build (the first one pulls `python:3.12-slim`, 1 to 2 minutes). Report the commit.

### 2. Set the real admin password (the user does this)

Ask the user to run, in a terminal you can see or on their own, and to type the password twice when asked:

```powershell
cd D:\temp\_docker\pluteus
docker compose run --rm pluteus python -m pluteus user set admin --password --config /config/config.toml
```

Wait for the user's confirmation. Do not continue before the placeholder password is replaced.

### 3. Start and verify on the LAN

```powershell
docker compose up -d
docker compose ps
docker compose logs --tail 30 pluteus
curl.exe http://localhost:8100/healthz
```

Expected: container `healthy` within about 30 seconds (the health check runs every 30 s), `healthz` prints `ok`, and the log
ends with `Application startup complete`. If it is unhealthy, read the log, fix what the log shows (do not guess), and report.

Then ask the user to open `http://192.168.1.99:8100/pluteus/` and check: sign in as `admin`; the folders `books`,
`comics` and `uploads` are listed; an EPUB opens in the reader; a folder with many subfolders opens; an upload (any small
file) shows up under `D:\temp\_docker\pluteus\uploads\admin\`.

### 4. Measure the speed (the open question about Docker on Windows)

pluteus reads the folders live. Docker Desktop reaches Windows drives through a slower layer, so measure. The user chooses a
big folder; replace `YOURPASSWORD` interactively (do not write the real password into a file or into this conversation):

```powershell
curl.exe -u admin:YOURPASSWORD -s -o NUL -w "%{time_total}s`n" http://localhost:8100/pluteus/opds/books
curl.exe -u admin:YOURPASSWORD -s -o NUL -w "%{time_total}s`n" http://localhost:8100/pluteus/opds/comics
```

Run each **twice** (first run cold, second warm) and also for one large sub-folder (add its path after `/opds/books/`,
percent-encoded). Report all numbers. Rough expectation from the other machine over SMB: 10 to 40 ms per feed page, about
2 seconds cold for a comic feed page with many CBZ files. A cold time of several seconds for a normal folder means the Docker
mount is slow; say so, and mention that running pluteus natively on Windows (see `docs/running.md`, "Native") is the fallback.

### 5. Add the other people (ask the user for names, roles and which folders each may open)

```powershell
docker compose exec pluteus python -m pluteus user add NAME --roots books --config /config/config.toml
docker compose exec pluteus python -m pluteus user list --config /config/config.toml
```

Each command prompts for the password (the user types it). `--roots books` limits a user to one root, `--adult` lets a user
see folders marked with an `.adult` file, `--no-upload` removes their upload folder. Or let the user do it with
`D:\temp\_docker\pluteus\pluteus-admin.exe` (open `D:\temp\_docker\pluteus\config\config.toml`). User changes take effect
within seconds; any other change to `config.toml` needs `docker compose restart pluteus`.

### 6. Turn on the public route (ask first)

Only after steps 3 and 4 are good, and the user says go:

```powershell
docker exec caddy caddy validate --config /etc/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Check the other sites still answer (the user knows which ones matter; at least the main host and Ubooquity). The public
address is `https://<the host name in the Caddyfile>/pluteus/`.

### 7. Phone test (the user does it; you read the logs)

Follow `D:\Dev\pluteus\docs\https-and-phone-test.md`, step 3 onwards, on an Android phone **over mobile data**: sign in, upload a
photo, install the app (Chrome menu: Install app), then Share a photo from the gallery to pluteus. Plain upload from a phone
was already proven on the LAN; **installing the app and the Share target were not**: Chrome said "this app cannot be
installed" over plain LAN HTTP, probably because installing needs a publicly reachable HTTPS address (or a service worker,
which was left out). While the user tests, watch `docker compose logs -f pluteus`. pluteus logs one warning line for every
request it refuses as cross-site (`refused POST /share as cross-site: origin=... host=... sec-fetch-site=...`): collect those
lines and the Chrome version and send them to the user for the other Claude.

## Troubleshooting

| Symptom | Likely cause and action |
|---|---|
| `docker compose build` fails | read the first error; usually network (pulling the base image) or a dirty clone |
| Container restarts in a loop | `docker compose logs pluteus`; a config error prints a clear message (`check-config` rules) |
| `unhealthy` | wait one health interval (30 s); then the logs |
| Login works on the LAN, not through Caddy | `trust_proxy`, `base_path = "/pluteus"` in `config.toml`; confirm the Caddy block exists and was reloaded |
| 403 "Cross-site request refused" through Caddy | the proxy rewrote the Host header: add `allowed_hosts = ["<public host>"]` under `[server]`, restart |
| Library folders empty | wrong mount path in the compose file; `docker compose exec pluteus ls /library/books` |
| Every page answers 503 | the mounted drive is not reachable inside Docker (Docker Desktop file sharing); report it |
| Uploads fail with a permissions error | the uploads folder must be writable by uid 10001 (not normally an issue on Docker Desktop for Windows) |
| First open of a big comic folder is slow | see step 4; report the numbers |

## What to report back (so the other Claude can continue)

1. The commit that was built, and that the container is healthy.
2. The timing numbers from step 4 (cold and warm, per folder).
3. The users that exist now (names and folders only, never passwords), and whether the admin password was changed.
4. Whether the Caddy reload happened, and that the other sites still work.
5. Everything from the phone test: Chrome version, what the Install menu offered, what Share did, and any
   `refused ... as cross-site` log lines.
6. Anything that surprised you or that you changed (with the reason). Do not leave changes unreported.

## Later, not now

Native install with auto-start (`tools\install-task.ps1`, not needed with Docker), and updating: `git pull` in
`D:\Dev\pluteus`, then `docker compose up -d --build` in `D:\temp\_docker\pluteus`. State survives in the `pluteus-data` volume.
