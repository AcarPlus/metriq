"""In-app updater: asks GitHub for the latest release, downloads the Setup and starts it (stdlib only).

Safety: only https downloads from GitHub hosts (every redirect hop is checked), the file must match the size and
the SHA-256 digest GitHub publishes for the asset; without a digest nothing is installed.
Testing: METRIQ_FAKE_VERSION=<x.y.z> overrides the current version, METRIQ_UPDATE_DRYRUN=1 makes
install() only log the command instead of starting the Setup."""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.github.com/repos/AcarPlus/metriq/releases/latest"
ASSET_RE = re.compile(r"^metriQ-Setup-.*\.exe$")
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
INSTALL_ARGS = "/SILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE"
TIMEOUT = 6
CHUNK = 64 * 1024


class UpdateError(Exception):
    pass


def current_version():
    """Installed version (METRIQ_FAKE_VERSION overrides it for testing)."""
    fake = os.environ.get("METRIQ_FAKE_VERSION", "").strip()
    if fake:
        return fake
    from version import __version__
    return __version__


def parse_version(s):
    """'v1.2.3' -> (1, 2, 3); None when it is not a plain numeric version."""
    m = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)\s*$", str(s or ""))
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:      # 1.2 == 1.2.0
        parts.pop()
    return tuple(parts)


def _host_ok(url):
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in ALLOWED_HOSTS and not u.username


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _host_ok(newurl):
            raise UpdateError("redirect to a host that is not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_SafeRedirect())


def _headers(ver, accept):
    return {"User-Agent": f"metriQ/{ver}", "Accept": accept}


def check(current):
    """None when up to date (or on any problem), else dict(version, tag, setup_url, setup_name, size, sha256, page_url)."""
    cur = parse_version(current)
    if cur is None:
        return None
    req = urllib.request.Request(API_URL, headers=_headers(current, "application/vnd.github+json"))
    with urllib.request.build_opener().open(req, timeout=TIMEOUT) as r:
        rel = json.loads(r.read(2_000_000).decode("utf-8"))
    if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
        return None
    tag = str(rel.get("tag_name") or "")
    new = parse_version(tag)
    if new is None or new <= cur:
        return None
    for a in rel.get("assets") or []:
        name = str(a.get("name") or "")
        url = str(a.get("browser_download_url") or "")
        if not ASSET_RE.match(name) or not _host_ok(url):
            continue
        digest = str(a.get("digest") or "").lower()
        sha = digest[7:] if digest.startswith("sha256:") else ""
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            sha = ""
        page = str(rel.get("html_url") or "")
        if not _host_ok(page):
            page = "https://github.com/AcarPlus/metriq/releases/latest"
        return {"version": tag.strip().lstrip("vV"), "tag": tag, "setup_url": url, "setup_name": name,
                "size": int(a.get("size") or 0), "sha256": sha, "page_url": page}
    return None


def download(info, progress_cb=None):
    """Downloads the Setup into a fresh folder under %TEMP%, verifies size and SHA-256; returns the file path."""
    if not info.get("sha256"):
        raise UpdateError("no SHA-256 digest published for this release: refusing to install")
    url = info["setup_url"]
    if not _host_ok(url) or not ASSET_RE.match(info.get("setup_name", "")):
        raise UpdateError("download address not allowed")
    folder = tempfile.mkdtemp(prefix="metriQ-update-")
    path = os.path.join(folder, os.path.basename(info["setup_name"]))
    try:
        req = urllib.request.Request(url, headers=_headers(current_version(), "application/octet-stream"))
        h = hashlib.sha256()
        n = 0
        with _opener().open(req, timeout=15) as r, open(path, "wb") as f:
            if not _host_ok(r.geturl()):
                raise UpdateError("final host not allowed")
            total = info.get("size") or int(r.headers.get("Content-Length") or 0)
            while True:
                b = r.read(CHUNK)
                if not b:
                    break
                f.write(b)
                h.update(b)
                n += len(b)
                if progress_cb and total:
                    progress_cb(min(100, int(n * 100 / total)))
        if info.get("size") and n != info["size"]:
            raise UpdateError(f"size mismatch ({n} != {info['size']})")
        if h.hexdigest() != info["sha256"].lower():
            raise UpdateError("SHA-256 mismatch")
        if progress_cb:
            progress_cb(100)
        return path
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def install(path):
    """Starts the Setup silently (Windows asks for administrator rights). True when it was launched."""
    cmd = f'"{path}" {INSTALL_ARGS}'
    if os.environ.get("METRIQ_UPDATE_DRYRUN") == "1":
        print("updater dry run, would start:", cmd, file=sys.stderr)
        return True
    import ctypes
    fn = ctypes.windll.shell32.ShellExecuteW
    fn.restype = ctypes.c_void_p
    rc = fn(None, "open", path, INSTALL_ARGS, os.path.dirname(path), 1)
    return (rc or 0) > 32
