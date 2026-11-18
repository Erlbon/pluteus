"""Kobo sync: a Kobo e-reader with its own (stock) reading software gets the books of the user's "Read next" list into its library,
and its reading position is kept here.

A Kobo talks to the Kobo store through one address, `api_endpoint` in `.kobo/Kobo/Kobo eReader.conf`. Pointed at
<site>/kobo/<app key> it asks pluteus instead: which books it should have (library sync), the details and download of each, its cover,
and where the reader is in it (reading state). The answers follow the store's own format, as Calibre-Web and Komga do.

What is synced is the user's "Read next" shelf, never a scan of the library: each EPUB on it, and the EPUBs directly inside a folder on it.
A book taken off the list is taken off the Kobo at its next sync; a book removed on the Kobo stays off it until it is put on the list again.
`.kepub.epub` files are sent as Kobo EPUBs (KEPUB), others as EPUB. Reading positions are kept per user and book, for Kobo devices: like
KOReader sync they are not connected to the web readers.

The key in the address is the user's app key (read-only for everything else). Wrong keys count against the address, like the other app logins.
The Kobo store itself is not reached through pluteus (no request leaves the server): while a Kobo is pointed here, its store, wishlist and
purchases do not sync.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import time
import uuid
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, RedirectResponse, Response

from .bodies import read_limited

log = logging.getLogger("pluteus.kobo")

KEY = re.compile(r"[A-Za-z0-9_-]{8,80}")
BOOK = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NAMESPACE = uuid.UUID("6f1d7a52-2d1b-4f57-9c1e-7a1c5e0b9d3a")      # pluteus' own: a book's id is uuid5(NAMESPACE, root + "/" + path)
STORE = "https://storeapi.kobo.com"
SHELF = "next"
PAGE = 100                       # entries in one answer of the library sync; the Kobo asks again while the answer says "continue"
MAX_BOOKS = 500                  # most books one user gets onto a Kobo (the shelf holds at most 500 entries as well)
MAX_IN_FOLDER = 100              # EPUBs taken from one folder on the shelf
MAX_STATE_BODY = 64 * 1024
STATUSES = ("ReadyToRead", "Reading", "Finished")
UNKNOWN = "00000000-0000-0000-0000-000000000001"


def is_kobo_book(name: str) -> bool:
    return name.lower().endswith(".epub")


def book_id(root: str, path: str) -> str:
    return str(uuid.uuid5(NAMESPACE, root + "/" + path))


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(0.0, t)))


def _json(body, status: int = 200, headers: dict | None = None) -> Response:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Response(data, status, media_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store", **(headers or {})})


def read_token(header: str) -> dict | None:
    """The sync token the Kobo sent back (the one we gave it last time), or None for none or one that is not ours (a fresh device,
    one that was reset or signed out, or one that synced with the real store): then everything is sent again."""
    if not header:
        return None
    try:
        data = json.loads(base64.b64decode(header.encode("ascii"), validate=True))
    except (ValueError, UnicodeError):
        return None
    if not isinstance(data, dict) or data.get("pluteus") != 1 or not isinstance(data.get("states"), (int, float)):
        return None
    return data


def make_token(states: float) -> str:
    return base64.b64encode(json.dumps({"pluteus": 1, "states": states}).encode("ascii")).decode("ascii")


def _number(value, low: float, high: float):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ValueError("not a number")
    return min(high, max(low, value))


def _text(value, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("bad text")
    return value


def merge_state(old: dict | None, sent: dict, now: float) -> tuple[dict, dict]:
    """(the stored state after one ReadingState the Kobo PUT, the per-part results to answer). Raises ValueError for a bad body."""
    if not isinstance(sent, dict):
        raise ValueError("not an object")
    st = dict(old or {"created": now, "status": "ReadyToRead", "started": 0})
    results = {}
    mark = sent.get("CurrentBookmark")
    if isinstance(mark, dict):
        new = {}
        for k in ("ProgressPercent", "ContentSourceProgressPercent"):
            if mark.get(k) is not None:
                new[k] = _number(mark[k], 0, 100)
        loc = mark.get("Location")
        if isinstance(loc, dict) and loc.get("Value"):
            new["Location"] = {"Value": _text(loc.get("Value"), 1024), "Type": _text(loc.get("Type") or "", 64),
                               "Source": _text(loc.get("Source") or "", 1024)}
        st["bookmark"] = new
        results["CurrentBookmarkResult"] = {"Result": "Success"}
    stats = sent.get("Statistics")
    if isinstance(stats, dict):
        new = {}
        for k in ("SpentReadingMinutes", "RemainingTimeMinutes"):
            if stats.get(k) is not None:
                new[k] = int(_number(stats[k], 0, 10 ** 7))
        st["stats"] = new
        results["StatisticsResult"] = {"Result": "Success"}
    info = sent.get("StatusInfo")
    if isinstance(info, dict) and info.get("Status") is not None:
        status = info["Status"]
        if status not in STATUSES:
            raise ValueError("bad status")
        if status == "Reading" and st.get("status") != "Reading":
            st["started"] = int(st.get("started", 0)) + 1
            st["last_started"] = now
        st["status"] = status
        results["StatusInfoResult"] = {"Result": "Success"}
    st["updated"] = now
    return st, results


def state_answer(book: str, st: dict | None, created: float) -> dict:
    """One ReadingState as the Kobo wants it."""
    st = st or {}
    when = stamp(st.get("updated", created))
    status = {"LastModified": when, "Status": st.get("status", "ReadyToRead"), "TimesStartedReading": int(st.get("started", 0))}
    if st.get("last_started"):
        status["LastTimeStartedReading"] = stamp(st["last_started"])
    stats = {"LastModified": when, **{k: v for k, v in (st.get("stats") or {}).items() if v}}
    mark = {"LastModified": when}
    for k, v in (st.get("bookmark") or {}).items():
        mark[k] = int(v) if isinstance(v, float) and v == int(v) else v
    return {"EntitlementId": book, "Created": stamp(st.get("created", created)), "LastModified": when, "PriorityTimestamp": when,
            "StatusInfo": status, "Statistics": stats, "CurrentBookmark": mark}


def entitlement(book: str, created: float, modified: float, removed: bool = False) -> dict:
    return {"Accessibility": "Full", "ActivePeriod": {"From": stamp(time.time())}, "Created": stamp(created), "CrossRevisionId": book,
            "Id": book, "IsRemoved": removed, "IsHiddenFromArchive": False, "IsLocked": False, "LastModified": stamp(modified),
            "OriginCategory": "Imported", "RevisionId": book, "Status": "Active"}


def metadata(book: str, name: str, size: int, mtime: float, info: dict, url: str, fallback_title: str) -> dict:
    """BookMetadata: the title, authors and series read from the EPUB (meta.py), and where to download it."""
    kepub = name.lower().endswith(".kepub.epub")
    formats = ["KEPUB"] if kepub else ["EPUB3", "EPUB"]
    out = {"Categories": [UNKNOWN], "CoverImageId": book, "CrossRevisionId": book,
           "CurrentDisplayPrice": {"CurrencyCode": "USD", "TotalAmount": 0}, "CurrentLoveDisplayPrice": {"TotalAmount": 0},
           "Description": "", "DownloadUrls": [{"Format": f, "Size": size, "Url": url, "Platform": "Generic"} for f in formats],
           "EntitlementId": book, "ExternalIds": [], "Genre": UNKNOWN, "IsEligibleForKoboLove": False, "IsInternetArchive": False,
           "IsPreOrder": False, "IsSocialEnabled": True, "Language": (info.get("language") or "en")[:16], "PhoneticPronunciations": {},
           "PublicationDate": stamp(mtime), "Publisher": {"Imprint": "", "Name": ""}, "RevisionId": book,
           "Title": info.get("title") or fallback_title, "WorkId": book}
    if info.get("year"):
        out["PublicationDate"] = "%s-01-01T00:00:00Z" % info["year"]
    authors = info.get("authors") or []
    if authors:
        out["Contributors"] = authors
        out["ContributorRoles"] = [{"Name": a} for a in authors]
    if info.get("series"):
        series = {"Name": info["series"], "Id": str(uuid.uuid5(NAMESPACE, "series:" + info["series"]))}
        number = info.get("series_index") or ""
        try:
            series["NumberFloat"] = float(number)
            series["Number"] = number
        except ValueError:
            pass
        out["Series"] = series
    return out


class KoboSync:
    """The /kobo/<key>/... routes. `library` gives the books (see app.py: books, file, cover, meta)."""

    def __init__(self, cfg, auth, state, throttle, library):
        self.cfg, self.auth, self.state, self.throttle, self.library = cfg, auth, state, throttle, library

    # ---- who is asking ---------------------------------------------------------------------------------------------------
    def login(self, request):
        key = request.path_params["key"]
        if self.throttle.wait_key(request):
            return None
        name = self.auth.user_for_key(key) if KEY.fullmatch(key) else None
        user = self.auth.users.get(name) if name else None
        if user is None:
            self.throttle.fail_key(request)
            return None
        return self.auth.restricted(user)

    def origin(self, request) -> str:
        """This site's address as the Kobo reaches it, with the key: every address we give the Kobo starts with it."""
        proxied = self.cfg.trust_proxy
        scheme = (request.headers.get("x-forwarded-proto", "") if proxied else "").split(",")[0].strip() or request.url.scheme
        host = (request.headers.get("x-forwarded-host", "") if proxied else "").split(",")[0].strip() or request.headers.get("host", "")
        return "%s://%s%s/kobo/%s" % (scheme, host, self.cfg.base_path, request.path_params["key"])

    # ---- the books ---------------------------------------------------------------------------------------------------------
    def wanted(self, user) -> list:
        """[(book id, root, parts, FileInfo, time put on the shelf)] of the Read next shelf, in its order. Remembers each id."""
        out, seen = [], set()
        for root, parts, info, added in self.library.books(user, SHELF, MAX_BOOKS, MAX_IN_FOLDER):
            if not is_kobo_book(parts[-1]):
                continue
            book = book_id(root, "/".join(parts))
            if book in seen:
                continue
            seen.add(book)
            out.append((book, root, parts, info, added))
            if len(out) >= MAX_BOOKS:
                break
        self.state.kobo_remember(user.name, [(b, r, "/".join(p)) for b, r, p, _, _ in out])
        return out

    def find(self, user, book: str):
        """(root, parts, FileInfo) of a book id this user has had, or None (unknown, moved, gone, or no longer allowed)."""
        if not BOOK.fullmatch(book):
            return None
        where = self.state.kobo_where(user.name, book)
        if where is None:
            return None
        parts = tuple(where[1].split("/"))
        info = self.library.file(user, where[0], parts)
        return None if info is None else (where[0], parts, info)

    def book_metadata(self, request, book: str, root: str, parts, info) -> dict:
        meta = self.library.meta(info) or {}
        url = "%s/download/%s/%s" % (self.origin(request), book, quote(parts[-1]))
        title = parts[-1][:-len(".kepub.epub")] if parts[-1].lower().endswith(".kepub.epub") else parts[-1].rsplit(".", 1)[0]
        return metadata(book, parts[-1], info.size, info.mtime, meta, url, self.library.display(title))

    # ---- the routes ----------------------------------------------------------------------------------------------------------
    async def handle(self, request):
        user = await run_in_threadpool(self.login, request)
        if user is None:
            return _json({"message": "Unauthorized"}, 401)
        route = "/" + request.path_params.get("route", "").strip("/")
        method = request.method
        parts = route.split("/")
        if route == "/":
            return _json({})
        if route == "/v1/initialization":
            return self.initialization(request)
        if route in ("/v1/auth/device", "/v1/auth/refresh") and method == "POST":
            return await self.device_auth(request)
        if route == "/v1/library/sync" and method == "GET":
            return await run_in_threadpool(self.sync, request, user)
        if len(parts) == 5 and parts[1:3] == ["v1", "library"] and parts[4] == "metadata" and method == "GET":
            return await run_in_threadpool(self.metadata, request, user, parts[3])
        if len(parts) == 5 and parts[1:3] == ["v1", "library"] and parts[4] == "state" and method in ("GET", "PUT"):
            return await self.reading_state(request, user, parts[3])
        if len(parts) == 4 and parts[1:3] == ["v1", "library"] and BOOK.fullmatch(parts[3]) and method == "DELETE":
            await run_in_threadpool(self.state.kobo_removed, user.name, parts[3])
            return Response(status_code=204)
        if len(parts) >= 3 and parts[1] == "download" and method in ("GET", "HEAD"):
            return await self.download(user, parts[2])
        if len(parts) in (6, 7) and parts[-1] == "image.jpg" and method in ("GET", "HEAD"):
            return await self.cover(user, parts[1], parts[2])
        if route == "/v1/user/loyalty/benefits":
            return _json({"Benefits": {}})
        if route == "/v1/analytics/gettests":
            return _json({"Result": "Success", "TestKey": request.headers.get("x-kobo-userkey", "")[:100], "Tests": {}})
        if method == "GET" and not route.startswith("/v1/library/"):
            # The store's own pages (products, deals, profile ...): the Kobo is sent to the store. Nothing is fetched from here.
            return RedirectResponse(STORE + route + ("?" + request.url.query if request.url.query else ""), 307)
        return _json({})

    def initialization(self, request) -> Response:
        here = self.origin(request)
        res = dict(RESOURCES)
        res.update({"image_host": here.split("/kobo/")[0],
                    "image_url_template": here + "/{ImageId}/{Width}/{Height}/false/image.jpg",
                    "image_url_quality_template": here + "/{ImageId}/{Width}/{Height}/{Quality}/{IsGreyscale}/image.jpg",
                    "library_sync": here + "/v1/library/sync",
                    "library_metadata": here + "/v1/library/{Ids}/metadata",
                    "reading_state": here + "/v1/library/{Ids}/state",
                    "delete_entitlement": here + "/v1/library/{Ids}",
                    "device_auth": here + "/v1/auth/device",
                    "device_refresh": here + "/v1/auth/refresh",
                    "get_tests_request": here + "/v1/analytics/gettests",
                    "user_loyalty_benefits": here + "/v1/user/loyalty/benefits"})
        return _json({"Resources": res}, headers={"x-kobo-apitoken": "e30="})

    async def device_auth(self, request) -> Response:
        """The Kobo asks the store for tokens; it is already known by the key in its address, so it gets tokens that mean nothing."""
        raw = await read_limited(request, 16384) or b"{}"
        try:
            sent = json.loads(raw)
        except ValueError:
            sent = {}
        user_key = sent.get("UserKey", "") if isinstance(sent, dict) else ""
        return _json({"AccessToken": secrets.token_urlsafe(24), "RefreshToken": secrets.token_urlsafe(24), "TokenType": "Bearer",
                      "TrackingId": str(uuid.uuid4()), "UserKey": user_key if isinstance(user_key, str) else ""})

    def sync(self, request, user) -> Response:
        token = read_token(request.headers.get("x-kobo-synctoken", ""))
        now = time.time()
        if token is None:                                   # a new start: the Kobo has none of our books as far as we know
            self.state.kobo_forget(user.name)
            since = 0.0
        else:
            since = float(token["states"])
        wanted = self.wanted(user)
        had = self.state.kobo_sent(user.name)              # {book id: time removed on the Kobo, or None}
        # not yet on the Kobo; one removed on the Kobo comes back only when it was put on the list again after that
        pending = [w for w in wanted if w[0] not in had or (had[w[0]] is not None and w[4] > had[w[0]])]
        more = len(pending) > PAGE
        items, sent = [], []
        for book, root, parts, info, added in pending[:PAGE]:
            st = self.state.kobo_state(user.name, book)
            items.append({"NewEntitlement": {"BookEntitlement": entitlement(book, added, info.mtime),
                                             "BookMetadata": self.book_metadata(request, book, root, parts, info),
                                             "ReadingState": state_answer(book, st, added)}})
            sent.append(book)
        self.state.kobo_mark_sent(user.name, sent, now)
        if not more:
            keep = {b for b, *_ in wanted}
            for book, removed in had.items():
                if book not in keep and removed is None:   # taken off the list: taken off the Kobo
                    items.append({"ChangedEntitlement": {"BookEntitlement": entitlement(book, now, now, removed=True)}})
                    self.state.kobo_unsend(user.name, book)
            for book, st in self.state.kobo_states_since(user.name, since):
                if book in had and book not in sent and had[book] is None:
                    items.append({"ChangedReadingState": {"ReadingState": state_answer(book, st, st.get("created", now))}})
        headers = {"x-kobo-synctoken": make_token(since if more else now)}
        if more:
            headers["x-kobo-sync"] = "continue"
        log.info("kobo sync for %r: %d new, %d entries%s", user.name[:40], len(sent), len(items), " (more follow)" if more else "")
        return _json(items, headers=headers)

    def metadata(self, request, user, book: str) -> Response:
        found = self.find(user, book)
        if found is None:
            return _json({"message": "Not found"}, 404)
        return _json([self.book_metadata(request, book, *found)])

    async def reading_state(self, request, user, book: str) -> Response:
        if not BOOK.fullmatch(book) or await run_in_threadpool(self.state.kobo_where, user.name, book) is None:
            return _json({"message": "Not found"}, 404)
        if request.method == "GET":
            st = await run_in_threadpool(self.state.kobo_state, user.name, book)
            return _json([state_answer(book, st, time.time())])
        raw = await read_limited(request, MAX_STATE_BODY)
        try:
            body = json.loads(raw) if raw is not None else None
            sent = body["ReadingStates"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            return _json({"message": "Expected {\"ReadingStates\": [...]}"}, 400)
        now = time.time()
        old = await run_in_threadpool(self.state.kobo_state, user.name, book)
        try:
            st, results = merge_state(old, sent, now)
        except (ValueError, TypeError):
            return _json({"message": "A value in the reading state is not valid"}, 400)
        await run_in_threadpool(self.state.kobo_put_state, user.name, book, st, now)
        return _json({"RequestResult": "Success",
                      "UpdateResults": [{"EntitlementId": book, "LastModified": stamp(now), "PriorityTimestamp": stamp(now), **results}]})

    async def download(self, user, book: str) -> Response:
        found = await run_in_threadpool(self.find, user, book)
        if found is None:
            return _json({"message": "Not found"}, 404)
        _, parts, info = found
        return FileResponse(info.path, media_type="application/epub+zip", filename=parts[-1])

    async def cover(self, user, book: str, width: str) -> Response:
        found = await run_in_threadpool(self.find, user, book)
        if found is None:
            return Response("Not found", 404, media_type="text/plain")
        root, _, info = found
        try:
            wide = max(60, min(900, int(width)))
        except ValueError:
            wide = 600
        picture = await self.library.cover(info, root, wide)
        if picture is None:
            return Response("No cover", 404, media_type="text/plain")
        return Response(picture[0], media_type=picture[1], headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


# The store's list of addresses, as a Kobo receives it when it starts (the same list Calibre-Web and Komga answer with when they do not
# ask the store). initialization() points the library, cover and login addresses at pluteus; the store's own pages stay with Kobo.
RESOURCES = {
    "account_page": "https://www.kobo.com/account/settings",
    "account_page_rakuten": "https://my.rakuten.co.jp/",
    "add_device": STORE + "/v1/user/add-device",
    "add_entitlement": STORE + "/v1/library/{RevisionIds}",
    "affiliaterequest": STORE + "/v1/affiliate",
    "assets": STORE + "/v1/assets",
    "audiobook": STORE + "/v1/products/audiobooks/{ProductId}",
    "audiobook_detail_page": "https://www.kobo.com/{region}/{language}/audiobook/{slug}",
    "audiobook_landing_page": "https://www.kobo.com/{region}/{language}/audiobooks",
    "audiobook_preview": STORE + "/v1/products/audiobooks/{Id}/preview",
    "audiobook_purchase_withcredit": STORE + "/v1/store/audiobook/{Id}",
    "audiobook_subscription_orange_deal_inclusion_url": "https://authorize.kobo.com/inclusion",
    "authorproduct_recommendations": STORE + "/v1/products/books/authors/recommendations",
    "autocomplete": STORE + "/v1/products/autocomplete",
    "blackstone_header": {"key": "x-amz-request-payer", "value": "requester"},
    "book": STORE + "/v1/products/books/{ProductId}",
    "book_detail_page": "https://www.kobo.com/{region}/{language}/ebook/{slug}",
    "book_detail_page_rakuten": "http://books.rakuten.co.jp/rk/{crossrevisionid}",
    "book_landing_page": "https://www.kobo.com/ebooks",
    "book_subscription": STORE + "/v1/products/books/subscriptions",
    "browse_history": STORE + "/v1/user/browsehistory",
    "categories": STORE + "/v1/categories",
    "categories_page": "https://www.kobo.com/ebooks/categories",
    "category": STORE + "/v1/categories/{CategoryId}",
    "category_featured_lists": STORE + "/v1/categories/{CategoryId}/featured",
    "category_products": STORE + "/v1/categories/{CategoryId}/products",
    "checkout_borrowed_book": STORE + "/v1/library/borrow",
    "client_authd_referral": "https://authorize.kobo.com/api/AuthenticatedReferral/client/v1/getLink",
    "configuration_data": STORE + "/v1/configuration",
    "content_access_book": STORE + "/v1/products/books/{ProductId}/access",
    "daily_deal": STORE + "/v1/products/dailydeal",
    "deals": STORE + "/v1/deals",
    "delete_entitlement": STORE + "/v1/library/{Ids}",
    "delete_tag": STORE + "/v1/library/tags/{TagId}",
    "delete_tag_items": STORE + "/v1/library/tags/{TagId}/items/delete",
    "device_auth": STORE + "/v1/auth/device",
    "device_refresh": STORE + "/v1/auth/refresh",
    "dictionary_host": "https://ereaderfiles.kobo.com",
    "discovery_host": "https://discovery.kobobooks.com",
    "ereaderdevices": STORE + "/v2/products/EReaderDeviceFeeds",
    "eula_page": "https://www.kobo.com/termsofuse?style=onestore",
    "exchange_auth": STORE + "/v1/auth/exchange",
    "external_book": STORE + "/v1/products/books/external/{Ids}",
    "featured_list": STORE + "/v1/products/featured/{FeaturedListId}",
    "featured_lists": STORE + "/v1/products/featured",
    "fte_feedback": STORE + "/v1/products/ftefeedback",
    "funnel_metrics": STORE + "/v1/funnelmetrics",
    "get_download_keys": STORE + "/v1/library/downloadkeys",
    "get_download_link": STORE + "/v1/library/downloadlink",
    "get_tests_request": STORE + "/v1/analytics/gettests",
    "giftcard_epd_redeem_url": "https://www.kobo.com/{storefront}/{language}/redeem-ereader",
    "giftcard_redeem_url": "https://www.kobo.com/{storefront}/{language}/redeem",
    "gpb_flow_enabled": "False",
    "help_page": "http://www.kobo.com/help",
    "image_host": "//cdn.kobo.com/book-images/",
    "image_url_quality_template": "https://cdn.kobo.com/book-images/{ImageId}/{Width}/{Height}/{Quality}/{IsGreyscale}/image.jpg",
    "image_url_template": "https://cdn.kobo.com/book-images/{ImageId}/{Width}/{Height}/false/image.jpg",
    "instapaper_enabled": "True",
    "instapaper_env_url": "https://www.instapaper.com/api/kobo",
    "instapaper_link_account_start": "https://authorize.kobo.com/{region}/{language}/linkinstapaper",
    "kobo_audiobooks_credit_redemption": "False",
    "kobo_audiobooks_enabled": "False",
    "kobo_audiobooks_orange_deal_enabled": "False",
    "kobo_audiobooks_subscriptions_enabled": "False",
    "kobo_display_price": "False",
    "kobo_dropbox_link_account_enabled": "False",
    "kobo_google_tax": "False",
    "kobo_googledrive_link_account_enabled": "False",
    "kobo_nativeborrow_enabled": "False",
    "kobo_onedrive_link_account_enabled": "False",
    "kobo_onestorelibrary_enabled": "False",
    "kobo_privacyCentre_url": "https://www.kobo.com/privacy",
    "kobo_redeem_enabled": "False",
    "kobo_shelfie_enabled": "False",
    "kobo_subscriptions_enabled": "False",
    "kobo_superpoints_enabled": "False",
    "kobo_wishlist_enabled": "False",
    "library_book": STORE + "/v1/user/library/books/{LibraryItemId}",
    "library_items": STORE + "/v1/user/library",
    "library_metadata": STORE + "/v1/library/{Ids}/metadata",
    "library_prices": STORE + "/v1/user/library/previews/prices",
    "library_search": STORE + "/v1/library/search",
    "library_sync": STORE + "/v1/library/sync",
    "love_dashboard_page": "https://www.kobo.com/{region}/{language}/kobosuperpoints",
    "love_points_redemption_page": "https://www.kobo.com/{region}/{language}/KoboSuperPointsRedemption?productId={ProductId}",
    "magazine_landing_page": "https://www.kobo.com/emagazines",
    "notebooks": STORE + "/api/internal/notebooks",
    "notifications_registration_issue": STORE + "/v1/notifications/registration",
    "oauth_host": "https://oauth.kobo.com",
    "password_retrieval_page": "https://www.kobo.com/passwordretrieval.html",
    "personalizedrecommendations": STORE + "/v2/users/personalizedrecommendations",
    "pocket_link_account_start": "https://authorize.kobo.com/{region}/{language}/linkpocket",
    "post_analytics_event": STORE + "/v1/analytics/event",
    "ppx_purchasing_url": "https://purchasing.kobo.com",
    "privacy_page": "https://www.kobo.com/privacypolicy?style=onestore",
    "product_nextread": STORE + "/v1/products/{ProductIds}/nextread",
    "product_prices": STORE + "/v1/products/{ProductIds}/prices",
    "product_recommendations": STORE + "/v1/products/{ProductId}/recommendations",
    "product_reviews": STORE + "/v1/products/{ProductIds}/reviews",
    "products": STORE + "/v1/products",
    "productsv2": STORE + "/v2/products",
    "provider_external_sign_in_page": "https://authorize.kobo.com/ExternalSignIn/{providerName}?returnUrl=http://kobo.com/",
    "quickbuy_checkout": STORE + "/v1/store/quickbuy/{PurchaseId}/checkout",
    "quickbuy_create": STORE + "/v1/store/quickbuy/purchase",
    "rakuten_token_exchange": STORE + "/v1/auth/rakuten_token_exchange",
    "rating": STORE + "/v1/products/{ProductId}/rating/{Rating}",
    "reading_services_host": "https://readingservices.kobo.com",
    "reading_state": STORE + "/v1/library/{Ids}/state",
    "redeem_interstitial_page": "https://www.kobo.com",
    "registration_page": "https://authorize.kobo.com/signup?returnUrl=http://kobo.com/",
    "related_items": STORE + "/v1/products/{Id}/related",
    "remaining_book_series": STORE + "/v1/products/books/series/{SeriesId}",
    "rename_tag": STORE + "/v1/library/tags/{TagId}",
    "review": STORE + "/v1/products/reviews/{ReviewId}",
    "review_sentiment": STORE + "/v1/products/reviews/{ReviewId}/sentiment/{Sentiment}",
    "shelfie_recommendations": STORE + "/v1/user/recommendations/shelfie",
    "sign_in_page": "https://authorize.kobo.com/signin?returnUrl=http://kobo.com/",
    "social_authorization_host": "https://social.kobobooks.com:8443",
    "social_host": "https://social.kobobooks.com",
    "store_home": "www.kobo.com/{region}/{language}",
    "store_host": "www.kobo.com",
    "store_newreleases": "https://www.kobo.com/{region}/{language}/List/new-releases/961XUjtsU0qxkFItWOutGA",
    "store_search": "https://www.kobo.com/{region}/{language}/Search?Query={query}",
    "store_top50": "https://www.kobo.com/{region}/{language}/ebooks/Top",
    "subs_landing_page": "https://www.kobo.com/{region}/{language}/plus",
    "subs_management_page": "https://www.kobo.com/{region}/{language}/account/subscriptions",
    "subs_plans_page": "https://www.kobo.com/{region}/{language}/plus/plans",
    "subs_purchase_buy_templated": "https://www.kobo.com/{region}/{language}/Checkoutoption/{ProductId}/{TierId}",
    "tag_items": STORE + "/v1/library/tags/{TagId}/Items",
    "tags": STORE + "/v1/library/tags",
    "taste_profile": STORE + "/v1/products/tasteprofile",
    "terms_of_sale_page": "https://authorize.kobo.com/{region}/{language}/terms/termsofsale",
    "update_accessibility_to_preview": STORE + "/v1/library/{EntitlementIds}/preview",
    "use_one_store": "True",
    "user_loyalty_benefits": STORE + "/v1/user/loyalty/benefits",
    "user_platform": STORE + "/v1/user/platform",
    "user_profile": STORE + "/v1/user/profile",
    "user_ratings": STORE + "/v1/user/ratings",
    "user_recommendations": STORE + "/v1/user/recommendations",
    "user_reviews": STORE + "/v1/user/reviews",
    "user_wishlist": STORE + "/v1/user/wishlist",
    "userguide_host": "https://ereaderfiles.kobo.com",
    "wishlist_page": "https://www.kobo.com/{region}/{language}/account/wishlist",
}
