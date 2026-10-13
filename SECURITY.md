# Security

Pluteus is meant to run on a home server and, behind HTTPS, to be reachable from the internet, so security reports are welcome.

**Report a problem privately** with GitHub's *Report a vulnerability* button on the repository's Security tab, not as a public issue.
Please include the version (shown in the footer and on the admin Status page), how you run it (Docker or native, behind which proxy)
and the steps to reproduce.

Only the **latest release** gets fixes. When a fix is released, a published GitHub security advisory names the affected versions; an
installation with `update_check = true` then shows a red notice on the admin Status page.

**Known trade-off:** an app key (admin page, Users, Make a key) is stored in clear text in `state.sqlite3` and in its daily copies in
`backups/`, because the salted-token logins of Subsonic apps and KOReader cannot be checked against a hash. The key can only read, is never shown
again after it is made and never written to the log, and can be revoked at once. Keep `data_dir` protected like a password file.

What pluteus is built to hold up against, and what it is not, is described in the README (sections on running it on the internet and on
the admin page). The admin page is for the local network only: never route or forward its port to the internet.
