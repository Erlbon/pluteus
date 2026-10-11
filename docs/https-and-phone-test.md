# Behind Caddy (HTTPS), and testing "Share to pluteus" on an Android phone

pluteus speaks plain HTTP. Caddy sits in front, gets the certificate and adds HTTPS. HTTPS is not optional here: browsers
only install a web app, and only offer its share target, from a secure address.

## 1. pluteus settings

In `config.toml`, under `[server]` (restart pluteus after changing them):

```toml
host = "127.0.0.1"            # only Caddy, on the same machine, talks to it (use "0.0.0.0" inside Docker)
trust_proxy = true            # believe Caddy's X-Forwarded-For / -Proto, so login throttling sees the real phone
cookie_secure = "always"      # the login cookie is only ever sent over HTTPS
uploads_dir = 'D:\pluteus-uploads'
# allowed_hosts = ["books.example.org"]   # only if a login or upload answers 403 behind the proxy
```

Check it: `python -m pluteus check-config --config config.toml`

## 2. The Caddyfile

**Own host name (simplest, recommended):**

```caddyfile
books.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy fetches and renews the certificate itself (ports 80 and 443 must reach the Caddy machine, and the DNS name must
point at your router/IP). It does not buffer uploads and has no body-size limit by default, so large uploads stream
straight through. If your Caddy was configured with a limit, raise it:

```caddyfile
books.example.org {
    request_body {
        max_size 5GB
    }
    reverse_proxy 127.0.0.1:8080
}
```

**Under a path on a host you already use** (like your Ubooquity at `/ubooquity`):

```caddyfile
example.org {
    handle_path /library/* {
        reverse_proxy 127.0.0.1:8080
    }
}
```

and add `base_path = "/library"` to pluteus. (`base_path` works whether Caddy strips the prefix, as above, or passes it on.)
A host of its own is still easier: the installed app and the share target then live at the root.

**Docker:** `reverse_proxy` to the published port (`127.0.0.1:8080`), or to the container name if Caddy is on the same
Docker network. Keep `host = "0.0.0.0"` inside the container but publish the port on `127.0.0.1` only
(`"127.0.0.1:8080:8080"`), so nothing reaches pluteus except through Caddy.

Quick check from any computer: open `https://books.example.org/healthz`: it should say `ok` with a valid padlock.

## 3. The phone test (Android, Chrome)

You need: the phone, one pluteus user, a photo or a PDF in the phone's gallery or Files app. Do the steps in order and
note where the first one fails.

1. **Open and sign in.** In Chrome open `https://books.example.org`. Sign in. The library list should include **uploads**.
2. **Plain upload.** Open **uploads**, tap the file chooser, pick a photo, tap **Upload**. Expected: a progress bar, then
   "Uploaded 1 file." and the file in the list. Repeat once with a big file (100 MB or more) on **mobile data**, not
   Wi-Fi. That tests Caddy, the router and the connection in one go. A failure here is a Caddy/limit/timeout problem,
   not a share-target problem.
3. **Install.** Chrome menu (three dots) -> **Install app** (sometimes **Add to Home screen** -> **Install**).
   Note exactly what the menu offers. If it only offers **Add shortcut**, Chrome does not consider pluteus installable:
   stop and send me that, because then it needs a small service worker (I left it out on purpose to stay light; I do not
   know whether your Chrome version still demands one).
4. **Open the installed app** from the home screen: it opens without the address bar and you should still be signed in.
5. **Share.** In the gallery or Files app select a photo or a PDF, tap **Share**, and look for **pluteus** in the list
   (it can take a minute after installing, and sometimes only appears after the share sheet has been opened once).
   Tap it. Expected: pluteus opens on **uploads** with "Uploaded 1 file." and the file is in the list.
6. **Share two files at once**, and a file with an odd name (spaces, an accented letter). Both should arrive.

### If something fails

| What you see | Most likely cause | What to do |
|---|---|---|
| Step 2 fails on mobile data but works on Wi-Fi | router/provider, or a Caddy timeout or size limit | note the file size and where it stopped (see "Reading the logs") |
| 403 "Cross-site request refused" when sharing | the share did not carry the headers the server checks | send me the log line (below) |
| The share lands on the sign-in page | the login cookie was not sent with the share request | send me the log line; do not sign in again and retry first, so the log shows the original attempt |
| pluteus is not in the share list | not installed as an app, or the phone cached the old manifest | remove the app, clear Chrome's site data for the host, install again; check `chrome://webapks` |
| Login works but nothing stays signed in | `cookie_secure = "always"` but the page is served over HTTP, or `trust_proxy` is off | check the address starts with `https://` |

### Reading the logs

pluteus prints one warning line for every request it refuses as cross-site, with the three headers that decided it
(`origin`, `host`, `sec-fetch-site`; never cookies or passwords):

```
refused POST /share as cross-site: origin=None host='books.example.org' sec-fetch-site='cross-site'
```

Native: look at the console where `pluteus serve` runs. Docker: `docker compose logs -f pluteus`.
Caddy's own log shows the status code of every request (`journalctl -u caddy`, or Caddy's log file).

Send me that line, the Chrome version (Chrome menu -> Settings -> About Chrome) and which numbered step failed.

## 4. Testing without a public host name (only to try the share target)

On the phone, in Chrome open `chrome://flags`, search for **Insecure origins treated as secure**, enable it, add
`http://192.168.x.x:8080` (the address of the machine running pluteus), tap **Relaunch**, then follow step 3 above using that
address. This is a test shortcut for one phone: it does not make anything safer, and it is not a way to run pluteus.
(`cookie_secure = "auto"` is needed for this test, since the page is HTTP.)
