# Benchmark results (12 October 2026)

Raw results behind the comparison page at https://erlbon.github.io/pluteus/compare/ (written up there, with the method and its
limits). One JSON file per product and library size:

- `12k-*.json`: 6,000 CBZ comics (with ComicInfo.xml) and 6,000 EPUBs (with series metadata), 284 MB, all invented.
- `3k-*.json`: 1,500 comics and 1,500 EPUBs, 71 MB.

Fields: `up_s` (seconds until the web server answered), `listed_s` / `scan_s` (seconds until the library was browsable or the scan
had gone quiet), `idle_mb`, `after_scan_mb`, `scan_peak_mb`, `peak_mb`, `after_load_mb` (container memory as `docker stats`
reports it), `crawl1` / `crawl2` (an OPDS crawl by a script, 8 requests at a time: request count, seconds, and the median,
95th-percentile and maximum latency in milliseconds, first and second pass), `db_mb` (state kept on disk). Docker Desktop on
Windows, libraries bind-mounted read-only, one product at a time from a fresh container, default settings, one run each.
Images: Pluteus 0.4.0 (215 MB), linuxserver/ubooquity 3.1.0-ls95 (371 MB), gotson/komga 26.04 (809 MB),
jvmilazz0/kavita latest of 2026-09-02 (900 MB).

Kavita did not index the EPUB library in our setup (`cancelled_after_s`): read its column as incomplete, not as a verdict.
Ubooquity's OPDS feed is off by default; it was switched on in its config file before the browsing crawl.
