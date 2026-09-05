"""Merchant Fix: null-route assetdelivery.roblox.com via the hosts file and clear
Roblox's asset cache (rbx-storage.*).

Blocking asset delivery forces Roblox to fail loading clothing/appearance assets,
which is what JARAM's merchant / player-join log detection keys off of.

Editing the hosts file needs Administrator rights; callers handle PermissionError.
"""
import os
import glob

BLOCK_IP = "255.255.255.0"
BLOCK_HOST = "assetdelivery.roblox.com"
HOSTS_LINE = f"{BLOCK_IP} {BLOCK_HOST}"
MARKER = "# JARAM Merchant Fix"


def hosts_path() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", "drivers", "etc", "hosts")


def _is_block_line(line: str) -> bool:
    parts = line.split("#", 1)[0].split()
    return len(parts) >= 2 and parts[0] == BLOCK_IP and parts[1].lower() == BLOCK_HOST


def is_enabled(path: str | None = None) -> bool:
    p = path or hosts_path()
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return any(_is_block_line(ln) for ln in f)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable_hosts(path: str | None = None) -> bool:
    """Append the block line if absent. Returns True if a change was made."""
    p = path or hosts_path()
    if is_enabled(p):
        return False
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    sep = "" if (content == "" or content.endswith("\n")) else "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{sep}{MARKER}\n{HOSTS_LINE}\n")
    return True


def disable_hosts(path: str | None = None) -> bool:
    """Remove the block line (and our marker). Returns True if a change was made."""
    p = path or hosts_path()
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False
    kept = [ln for ln in lines if not _is_block_line(ln) and ln.strip() != MARKER]
    if len(kept) == len(lines):
        return False
    with open(p, "w", encoding="utf-8") as f:
        f.writelines(kept)
    return True


def roblox_cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(base, "Roblox")


def clear_roblox_cache(cache_dir: str | None = None):
    """Delete rbx-storage.* files. Returns (deleted_count, [error_strings])."""
    d = cache_dir or roblox_cache_dir()
    deleted = 0
    errors = []
    for f in glob.glob(os.path.join(d, "rbx-storage.*")):
        try:
            os.remove(f)
            deleted += 1
        except Exception as e:
            errors.append(f"{os.path.basename(f)}: {e}")
    return deleted, errors


def demo() -> None:
    import tempfile
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("127.0.0.1 localhost\n")  # no trailing content issues
        assert is_enabled(tmp) is False
        assert enable_hosts(tmp) is True
        assert is_enabled(tmp) is True
        assert enable_hosts(tmp) is False          # idempotent
        body = open(tmp, encoding="utf-8").read()
        assert HOSTS_LINE in body and "127.0.0.1 localhost" in body
        assert disable_hosts(tmp) is True
        assert is_enabled(tmp) is False
        assert "127.0.0.1 localhost" in open(tmp, encoding="utf-8").read()  # untouched
        assert disable_hosts(tmp) is False         # nothing to remove
        # no-trailing-newline file must not merge lines
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("1.2.3.4 example.com")          # no newline
        enable_hosts(tmp)
        lines = open(tmp, encoding="utf-8").read().splitlines()
        assert "1.2.3.4 example.com" in lines and HOSTS_LINE in lines
        print("merchant_fix self-check OK")
    finally:
        os.remove(tmp)


if __name__ == "__main__":
    demo()
