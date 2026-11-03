"""Runnable check for _post_webhook retry logic. Run: python test_webhook_retry.py

Stubs requests.post + sleep so it runs offline in <1s. Asserts the retry/backoff
and give-up behavior the biome webhook path depends on.
"""
import requests
import multiscope
import webhook_gate
from multiscope import _post_webhook, PRIORITY_WEBHOOK_ATTEMPTS, NORMAL_WEBHOOK_ATTEMPTS

webhook_gate._sleep = lambda *_a, **_k: None  # retries live in webhook_gate now: no real backoff waits


class FakeResp:
    def __init__(self, code, retry_after=None):
        self.status_code = code
        if code == 429 and retry_after is None:
            retry_after = 0.01                    # keep the gate's 429 wait short
        self.headers = {} if retry_after is None else {"Retry-After": str(retry_after)}

    def json(self):
        raise ValueError("no body")


def run(sequence):
    """sequence: list of int codes or Exception instances to return/raise per call."""
    calls = {"n": 0}
    fails = {"n": 0, "last": None}

    def fake_post(url, **_kw):
        i = calls["n"]
        calls["n"] += 1
        item = sequence[min(i, len(sequence) - 1)]
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)

    def on_fail(label, status):
        fails["n"] += 1
        fails["last"] = status

    orig = requests.post
    requests.post = fake_post
    try:
        ok = _post_webhook("https://x", {}, attempts=5, on_fail=on_fail, label="t")
    finally:
        requests.post = orig
    return ok, calls["n"], fails["n"], fails["last"]


# 1) Immediate 2xx: one call, success, no on_fail.
ok, n, f, _ = run([204])
assert ok is True and n == 1 and f == 0, (ok, n, f)

# 2) 429, 429, then 200: retries and succeeds.
ok, n, f, _ = run([429, 429, 200])
assert ok is True and n == 3 and f == 0, (ok, n, f)

# 3) Persistent 500 with attempts=5: exhausts all, gives up, on_fail once.
ok, n, f, last = run([500])
assert ok is False and n == 5 and f == 1 and "500" in last, (ok, n, f, last)

# 4) 404 (dead hook): NOT retried, one call, on_fail once.
ok, n, f, last = run([404])
assert ok is False and n == 1 and f == 1 and "404" in last, (ok, n, f, last)

# 5) Network exception every time: retried up to attempts, then gives up.
ok, n, f, _ = run([ConnectionError("boom")])
assert ok is False and n == 5 and f == 1, (ok, n, f)

# 6) Tier constants wired as the user asked: rare biomes retry more.
assert PRIORITY_WEBHOOK_ATTEMPTS == 5 and NORMAL_WEBHOOK_ATTEMPTS == 3

print("all webhook retry checks passed")
