"""Discord webhook rate-limit gate: every webhook POST in the app goes through post().

- Sends to the same webhook are queued one at a time, so threads never burst one bucket.
- Discord's X-RateLimit-Remaining / Reset-After headers pause that webhook *before* it would 429.
- A 429 waits exactly retry_after (body or Retry-After) and resends; a global 429 pauses every webhook.
- 5xx and network errors back off and retry twice. A read timeout is not resent: Discord may
  already have posted it, and a resend would ping everyone twice.
- stats() counts sends for the performance page.
"""
import math
import threading
import time
from typing import Any, Dict, Optional

import requests

MAX_WAIT_S = 60.0               # give up on one message after waiting this long in total

_lock = threading.Lock()
_url_locks: Dict[str, threading.Lock] = {}
_blocked_until: Dict[str, float] = {}   # url -> monotonic time its bucket refills
_global_until = 0.0
_channel_cache: Dict[str, dict] = {}
# Set by the app: called (url, retry_after_s, is_global) on every real 429, from its own thread.
on_rate_limited = None
_stats = dict(delivered=0, failed=0, retried=0, rate_limited=0, http_attempts=0, waiting=0,
              last_http_status=None)


def _count(**changes: Any) -> None:
    with _lock:
        for k, v in changes.items():
            if k == "last_http_status":
                _stats[k] = v
            else:
                _stats[k] += v


def stats() -> dict:
    """Counters since start, plus how long until the slowest paused webhook may send again."""
    now = time.monotonic()
    with _lock:
        out = dict(_stats)
        out["global_cooldown_seconds"] = max(0.0, _global_until - now)
        out["destination_cooldown_seconds"] = max([0.0] + [t - now for t in _blocked_until.values()])
    return out


def channel_info(url: str) -> dict:
    """The webhook's {"channel_id", "name", "guild_id"} (a GET on the webhook URL needs no auth); cached."""
    with _lock:
        if url in _channel_cache:
            return _channel_cache[url]
    info: dict = {}
    try:
        r = requests.get(url, timeout=8)
        if r.ok:
            j = r.json() or {}
            info = {k: j.get(k) for k in ("channel_id", "name", "guild_id")}
    except Exception:
        info = {}
    if info:
        with _lock:
            _channel_cache[url] = info
    return info


def _tell_rate_limited(url: str, wait: float, is_global: bool) -> None:
    hook = on_rate_limited
    if hook is None:
        return

    def _call() -> None:
        try:
            hook(url, wait, is_global)
        except Exception:
            pass

    threading.Thread(target=_call, daemon=True, name="WebhookRateLimitNotice").start()


def _url_lock(url: str) -> threading.Lock:
    with _lock:
        return _url_locks.setdefault(url, threading.Lock())


def _wait_for(url: str, deadline: float) -> bool:
    """Sleep until `url` may send. False if that would pass `deadline`."""
    while True:
        with _lock:
            until = max(_blocked_until.get(url, 0.0), _global_until)
        now = time.monotonic()
        if until <= now:
            return True
        if until > deadline:
            return False
        _sleep(until - now)


def _note(url: str, r: requests.Response) -> None:
    """Pause the bucket per Discord's headers (or the 429 body)."""
    global _global_until
    h = r.headers
    wait, is_global = 0.0, False
    if r.status_code == 429:
        try:
            body = r.json() or {}
        except Exception:
            body = {}
        try:  # header first: always seconds (the body was milliseconds on old API versions)
            wait = float(h.get("Retry-After") or body.get("retry_after") or 1.0)
        except Exception:
            wait = 1.0
        if not math.isfinite(wait) or wait <= 0.0:
            wait = 1.0              # a 429 always waits: never resend in a tight loop
        is_global = bool(body.get("global")) or str(h.get("X-RateLimit-Global", "")).lower() == "true"
        _tell_rate_limited(url, wait, is_global)
    elif h.get("X-RateLimit-Remaining") == "0":
        try:
            wait = float(h.get("X-RateLimit-Reset-After") or 0.0)
        except Exception:
            wait = 0.0
    if not math.isfinite(wait) or wait <= 0.0:
        return
    until = time.monotonic() + min(wait, 600.0)
    with _lock:
        if is_global:
            _global_until = max(_global_until, until)
        else:
            _blocked_until[url] = max(_blocked_until.get(url, 0.0), until)


def _rewind(files: Any) -> None:
    for v in (files or {}).values() if isinstance(files, dict) else ():
        f = v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else v
        if hasattr(f, "seek"):
            f.seek(0)


_sleep = time.sleep


def post(url: str, *, json: Any = None, data: Any = None, files: Any = None, timeout: float = 10,
         max_wait_s: float = MAX_WAIT_S, retries: int = 2) -> requests.Response:
    """Drop-in for requests.post to a Discord webhook. Returns the final Response (2xx, a non-retryable
    4xx, or the last 429/5xx once `max_wait_s` is spent); raises if no response ever came back.

    ponytail: a webhook that stays rate limited holds its caller's thread for up to max_wait_s;
    a per-webhook queue thread if senders' pools ever starve.
    """
    deadline = time.monotonic() + float(max_wait_s)
    r: Optional[requests.Response] = None
    error: Optional[Exception] = None
    tries = 0
    limited = 0
    lock = _url_lock(url)
    _count(waiting=1)
    # Bounded: a caller (e.g. a UI-thread test button) never waits past its budget behind other senders.
    if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        _count(waiting=-1, failed=1)
        raise requests.exceptions.RetryError(f"webhook busy for over {max_wait_s:.0f}s")
    sends = 0
    try:
        while _wait_for(url, deadline):
            _rewind(files)
            sends += 1
            try:
                r = requests.post(url, json=json, data=data, files=files, timeout=timeout)
                error = None
            except (requests.RequestException, OSError) as e:
                r, error = None, e
            if r is not None:
                _note(url, r)
                _count(last_http_status=r.status_code)
                if r.status_code == 429:
                    _count(rate_limited=1)
                    limited += 1
                    if limited >= 5 or time.monotonic() >= deadline:
                        break
                    continue                    # _wait_for sleeps exactly retry_after
                if not 500 <= r.status_code < 600:
                    return r
            elif isinstance(error, requests.exceptions.ReadTimeout):
                break                           # may already be posted: never ping twice
            tries += 1
            if tries > int(retries) or time.monotonic() + 2.0 * tries > deadline:
                break
            _sleep(min(2.0 * tries, 8.0))
    finally:
        lock.release()
        ok = r is not None and 200 <= r.status_code < 300
        _count(waiting=-1, http_attempts=sends, retried=max(0, sends - 1),
               **{"delivered" if ok else "failed": 1})
    if r is not None:
        return r
    raise error or requests.exceptions.RetryError(f"webhook still rate limited after {max_wait_s:.0f}s")


if __name__ == "__main__":
    import http.server

    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            hits.append((self.path, time.monotonic()))
            n = sum(1 for p, _ in hits if p == self.path)
            if self.path == "/limited" and n == 1:      # first send: rate limited for 0.4 s
                body = b'{"retry_after": 0.4, "global": false}'
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404 if self.path == "/dead" else 204)
            if self.path == "/bucket":                  # bucket empties after each send
                self.send_header("X-RateLimit-Remaining", "0")
                self.send_header("X-RateLimit-Reset-After", "0.3")
            self.end_headers()

        def log_message(self, *a):
            pass

    told = []
    on_rate_limited = lambda u, w, g: told.append((u.rsplit("/", 1)[-1], w, g))
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_port

    t0 = time.monotonic()
    assert post(base + "/limited", json={}).status_code == 204       # resent after the 429
    assert time.monotonic() - t0 >= 0.39
    time.sleep(0.1)
    assert told == [("limited", 0.4, False)], told                    # the app hears about real 429s only
    assert post(base + "/dead", json={}).status_code == 404          # dead hook: no retries
    assert sum(1 for p, _ in hits if p == "/dead") == 1
    post(base + "/bucket", json={})
    post(base + "/bucket", json={})                                  # waits for the bucket to refill
    b = [t for p, t in hits if p == "/bucket"]
    assert b[1] - b[0] >= 0.29, b
    try:
        post("http://127.0.0.1:1/x", json={}, max_wait_s=3)
        raise AssertionError("expected a network error")
    except requests.RequestException:
        pass
    s = stats()
    assert (s["delivered"], s["failed"], s["rate_limited"], s["waiting"]) == (3, 2, 1, 0), s
    print("webhook_gate ok")
