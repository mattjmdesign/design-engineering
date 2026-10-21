"""_policy.py: what the measurement browser is allowed to touch.

Pure functions with no I/O, kept small so a reviewer can read the whole policy:

  * Start URLs must be file: or loopback http(s), unless --allow-remote is given.
  * Every request the page makes passes through request_decision(). Requests that
    would change server state (anything but GET and HEAD) are never sent: they are
    answered locally. Requests to hosts that are not loopback are blocked.
  * Evidence is written only below the evidence folder (safe_join).

URL parsing is done by hand on purpose. A host is trusted only when the whole
authority is plain: no user info, no backslashes, no escapes, and a host that is
localhost, a *.localhost name, 127.0.0.0/8, or ::1. Anything odd is treated as
remote, which is the safe direction.

Compatible with Python 3.9.
"""
import ipaddress
import os
import re

_URI = re.compile(r"^(?:([A-Za-z][A-Za-z0-9+.\-]*):)?(?://([^/?#]*))?([^?#]*)")
_PLAIN_AUTHORITY = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.\-]+)(:\d{1,5})?$")
NON_NETWORK_SCHEMES = ("data", "blob", "about")
WRITE_FREE_METHODS = ("GET", "HEAD")


class PolicyError(Exception):
    pass


def split_url(url):
    """Return (scheme, host, port, path). host is None when the URL has no authority or an unsafe one."""
    text = url.strip()
    match = _URI.match(text)
    scheme = (match.group(1) or "").lower()
    authority = match.group(2)
    path = match.group(3) or ""
    host = port = None
    if authority is not None:
        plain = _PLAIN_AUTHORITY.match(authority)
        if plain:
            host = plain.group(1).strip("[]").lower().rstrip(".")
            port = plain.group(2)[1:] if plain.group(2) else None
        elif authority == "":
            host = ""
    return scheme, host, port, path


def is_loopback_host(host):
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv4Address):
        return addr in ipaddress.ip_network("127.0.0.0/8")
    return addr == ipaddress.IPv6Address("::1")


def check_start_url(url, allow_remote=False):
    """Validate the URL a user asked to measure. Returns the URL to load, or raises PolicyError."""
    raw = (url or "").strip()
    if not raw:
        raise PolicyError("no URL given")
    scheme, host, port, path = split_url(raw)
    if not scheme and os.path.exists(raw):
        return "file://" + os.path.abspath(raw)
    if scheme == "file":
        if host not in ("", "localhost"):
            raise PolicyError("file URLs must not name a remote host")
        return raw
    if scheme in ("http", "https"):
        if is_loopback_host(host) or allow_remote:
            return raw
        raise PolicyError("refusing to load %s: only loopback hosts and file URLs are allowed (use --allow-remote to override)" % (host or "an unrecognised host"))
    raise PolicyError("unsupported URL scheme %r: use file:, http: or https:" % (scheme or raw[:20]))


def request_decision(url, method, allow_remote=False):
    """Decide what to do with one request: ('allow'|'stub'|'block', reason, host)."""
    scheme, host, _port, _path = split_url(url)
    verb = (method or "GET").upper()
    if scheme in NON_NETWORK_SCHEMES:
        return "allow", "not a network request", None
    if scheme == "file":
        if host not in ("", "localhost"):
            return "block", "file URL names a remote host", None
        if verb in WRITE_FREE_METHODS:
            return "allow", "local file", None
        return "stub", "write to a file URL", None
    if scheme in ("http", "https"):
        if verb not in WRITE_FREE_METHODS:
            return "stub", "%s would change server state, so it is answered locally" % verb, host
        if is_loopback_host(host):
            return "allow", "loopback", host
        if allow_remote:
            return "allow", "remote allowed by --allow-remote", host
        return "block", "host %s is not loopback" % (host or "unknown"), host
    return "block", "scheme %s is not allowed" % (scheme or "unknown"), host


_USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*://)[^/?#@]*@")


def redact_url(url):
    """The form of a URL that is safe to write into evidence: scheme, host, and path stay; user info and fragments are
    dropped; query keys stay but their values are replaced, because tokens often travel in query strings."""
    text = _USERINFO.sub(r"\1~@", str(url))
    base, hash_mark, _fragment = text.partition("#")
    path, question, query = base.partition("?")
    out = path
    if question:
        keys = [pair.split("=", 1)[0] for pair in query.split("&") if pair]
        out += "?" + "&".join(k + "=~" for k in keys) if keys else "?"
    return out + ("#~" if hash_mark else "")


def safe_join(root, *parts):
    """Join parts under root and refuse any result that escapes it."""
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, *parts))
    if target != base and not target.startswith(base + os.sep):
        raise PolicyError("refusing to write outside the evidence folder: %s" % os.path.join(*parts))
    return target
