# Running pluteus: Docker or native

Both work and neither is second class. Pick by what the machine already does:

| | Docker | Native (Python) |
|---|---|---|
| Good when | the machine already runs your other services in Docker; you want one folder to back up and a clean upgrade | the libraries are on local disks and you want the least moving parts, or Docker Desktop's drive layer is too slow |
| Needs | Docker (Desktop on Windows) | Python 3.11+ |
| Isolation | read-only container, no capabilities, own user | whatever the account you run it as can read |
| Upgrading | rebuild the image, `docker compose up -d` | `pip install --upgrade`, restart |
| Tested | yes (Windows, Docker Desktop; hardened settings; uploads) | yes for install and `serve`; auto-start below is **not tested** |

Everything else (config file, users, Caddy, uploads) is identical. The config file is the same format; only the paths in it
differ (container paths in Docker).

## Docker

Files: `Dockerfile`, `docker-compose.example.yml` (copy it to `docker-compose.yml`).

1. Put the source next to the compose file (or build from a copy of this repository): `build: .` in the example.
2. Make a `config/` folder with a `config.toml` (start from `config.example.toml`). Use **container paths** and these
   settings:

   ```toml
   [server]
   host = "0.0.0.0"
   port = 8080
   data_dir = "/data"
   uploads_dir = "/uploads"      # optional, see the Uploads section of the README
   trust_proxy = true            # only when a proxy such as Caddy is in front
   cookie_secure = "always"      # same condition: the proxy serves HTTPS

   [[roots]]
   name = "books"
   path = "/library/books"
   ```

3. In `docker-compose.yml` mount the libraries read-only, the config folder, a volume for `/data`, and (for uploads) a
   writable folder for `/uploads`. Publish the port:
   - **Behind a Caddy that is also in Docker Desktop and reaches other apps through `host.docker.internal`:** publish on the
     host, `"8100:8080"`, and point Caddy at `host.docker.internal:8100` (see `https-and-phone-test.md`, section 5).
   - **Behind Caddy on the same Docker network:** no `ports:` at all, and `reverse_proxy pluteus:8080` in the Caddyfile.
   - **Directly on the LAN:** `"8080:8080"`.
4. Create the first user, then start:

   ```bash
   docker compose run --rm pluteus python -m pluteus user add alice --config /config/config.toml
   docker compose up -d --build
   ```

   (`user add` asks for the password. Afterwards, add users with
   `docker compose exec pluteus python -m pluteus user add NAME --config /config/config.toml`; a running server notices
   within seconds.)

Notes:

- The container runs as **uid 10001**. On a **Linux host** the folders you mount for writing (`/uploads`, `/config`, and a
  bind-mounted `/data`) must be owned by that uid: `chown -R 10001 uploads config`. On Docker Desktop for Windows this was
  not needed (a Windows folder bind-mounted as `/uploads` took uploads fine).
- The example compose file is hardened (`read_only`, `cap_drop: ALL`, `no-new-privileges`, limits). Uploads do not need
  `/tmp`: they stream straight into the user's folder.
- Keep `/uploads` a separate folder from the libraries (pluteus refuses to start if they overlap).
- Speed on a library reached through Docker Desktop's Windows drive layer is **not benchmarked yet**; if a folder opens
  slowly, compare against a native run before blaming anything else.
- Upgrade: pull or copy the new source, then `docker compose up -d --build`. State lives in the `/data` volume, so
  logins and reading progress survive.

## Native

Windows (PowerShell) or Linux/macOS (the same, with `python3` and forward slashes):

```powershell
git clone <wherever the repository lives> C:\pluteus ; cd C:\pluteus
python -m venv .venv
.\.venv\Scripts\python -m pip install .          # installs pluteus and its two or three dependencies
copy config.example.toml config.toml              # edit: roots, data_dir, uploads_dir, ...
.\.venv\Scripts\python -m pluteus user add alice --config config.toml
.\.venv\Scripts\python -m pluteus check-config --config config.toml
.\.venv\Scripts\python -m pluteus serve --config config.toml
```

Behind Caddy set `host = "127.0.0.1"` (Caddy on the same machine) or `"0.0.0.0"` (Caddy in Docker Desktop reaching the
host through `host.docker.internal`; then keep the port closed on the router and allow it only locally in the firewall).

Starting at boot (**not tested yet, adapt before trusting**):

- **Windows**: a Task Scheduler task that runs `C:\pluteus\.venv\Scripts\python.exe -m pluteus serve --config
  C:\pluteus\config.toml` at startup, in `C:\pluteus`. Run it under an account that can read the libraries. A mapped drive
  letter (`Z:`) does not exist for a task or service account: use the real local path or a UNC path in `config.toml`.
  When a library folder is not reachable yet at boot, `serve` prints a warning and starts anyway (its pages answer 503
  until the folder is back); `check-config` is the strict one and reports it as an error.
- **Linux**: a systemd unit with `ExecStart=/opt/pluteus/.venv/bin/python -m pluteus serve --config /etc/pluteus/config.toml`,
  `User=pluteus`, `Restart=on-failure`.

## Moving between the two

The config file and the state are the same either way. Switching from native to Docker (or back): copy `config.toml`,
change the paths to the other side's paths, and copy the state folder (`data_dir`: `state.sqlite3` holds logins and reading
progress). The metadata cache is disposable and rebuilds itself.
