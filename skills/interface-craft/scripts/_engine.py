"""_engine.py: drives headless Chrome to capture one cell of the evidence matrix.

A cell is one page load at one viewport width and one color scheme, optionally in
one named state. For each cell the engine:

  1. opens an isolated browser context (no storage shared between cells),
  2. forces the viewport with Emulation.setDeviceMetricsOverride (the --window-size
     flag lays pages out at the wrong width in headless Chrome),
  3. routes every request through the request policy in _policy.py (loopback only,
     writes answered locally, optional per-state mock responses),
  4. waits for load, network quiet, fonts, and DOM quiet,
  5. takes screenshots, runs collect.js, asks Chrome for accessible names, and
     walks the page with real Tab key events.

Nothing here applies a threshold: it returns raw facts for _rules.py. Interaction
steps (click, type, press) run only when the caller passes them.

Compatible with Python 3.9.
"""
import base64
import fnmatch
import hashlib
import html
import json
import math
import os
import re
import time
from collections import Counter

import _cdp
import _png
import _policy
import _rules

HERE = os.path.dirname(os.path.abspath(__file__))

ANIMATION_CSS = ("*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;"
                 "transition-duration:0s!important;transition-delay:0s!important;scroll-behavior:auto!important;caret-color:transparent!important}")
SCROLLBAR_CSS = ("@layer ui-measure{::-webkit-scrollbar{width:%dpx;height:%dpx;background:#f1f1f1}"
                 "::-webkit-scrollbar-thumb{background:#c1c1c1}::-webkit-scrollbar-corner{background:#f1f1f1}}")
FAIL_REASONS = ("Failed", "Aborted", "TimedOut", "AccessDenied", "ConnectionClosed", "ConnectionReset", "ConnectionRefused",
                "ConnectionAborted", "ConnectionFailed", "NameNotResolved", "InternetDisconnected", "AddressUnreachable")
PSEUDO_STATES = ("hover", "focus-visible", "active", "focus", "focus-within")


class ToolError(Exception):
    """A problem the user can act on (bad URL, Chrome missing, bad spec). Exit status 2."""


class StepError(ToolError):
    pass


def clip_text(value, limit=160):
    text = " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(value)).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


# ----------------------------------------------------------------------------
# viewport and rule specs

_VIEW = re.compile(r"^(\d{2,4})(?:x(\d{2,4}))?(?:@(\d(?:\.\d+)?))?$")


def default_height(width, cfg):
    h = cfg.cap("heights")
    if width <= h["narrow_max_width"]:
        return h["narrow"]
    return h["mid"] if width <= h["mid_max_width"] else h["wide"]


def parse_viewport(token, cfg):
    match = _VIEW.match(token.strip())
    if not match:
        raise ToolError("bad viewport %r: use 390, 390x844, or 390x844@2" % token)
    width = int(match.group(1))
    return {"width": width, "height": int(match.group(2)) if match.group(2) else default_height(width, cfg),
            "dpr": float(match.group(3)) if match.group(3) else float(cfg.cap("dpr"))}


class NetRule(object):
    """One network override from a states spec: {match, method?, hang | fail | status/json/body}."""

    def __init__(self, spec):
        if not isinstance(spec, dict) or not isinstance(spec.get("match"), str) or not spec["match"]:
            raise ToolError("every net rule needs a non-empty \"match\" string")
        self.spec = spec
        self.method = (spec.get("method") or "").upper() or None
        pattern = spec["match"]
        if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
            try:
                rx = re.compile(pattern[1:-1])
            except re.error as error:
                raise ToolError("net rule match %s is not a valid pattern: %s" % (pattern, error))
            self._fn = lambda url: bool(rx.search(url))
        elif "*" in pattern:
            self._fn = lambda url: fnmatch.fnmatchcase(url, pattern)
        else:
            self._fn = lambda url: pattern in url
        if spec.get("hang"):
            self.action = "hang"
        elif "fail" in spec:
            self.action = "fail"
            self.reason = spec["fail"] if spec["fail"] in FAIL_REASONS else "ConnectionFailed"
        elif any(k in spec for k in ("status", "json", "body")):
            self.action = "respond"
        else:
            raise ToolError("net rule %r needs one of hang, fail, status, json, or body" % pattern)
        self.hits = 0

    def matches(self, url, method):
        return (self.method is None or self.method == method) and self._fn(url)


def cors_headers(req_headers, preflight=False):
    lowered = dict((k.lower(), v) for k, v in (req_headers or {}).items())
    origin = lowered.get("origin")
    out = [("Access-Control-Allow-Origin", origin or "*")]
    if origin:
        out += [("Access-Control-Allow-Credentials", "true"), ("Vary", "Origin")]
    out.append(("Access-Control-Allow-Headers", lowered.get("access-control-request-headers") or "*"))
    out.append(("Access-Control-Allow-Methods", lowered.get("access-control-request-method") or "GET, POST, PUT, PATCH, DELETE, OPTIONS"))
    if preflight:
        out.append(("Access-Control-Max-Age", "60"))
    return out


# ----------------------------------------------------------------------------
# storage seeding

def origin_of(url):
    """The origin storage seeding is limited to: scheme://host[:port], or "file:" for local files."""
    scheme, host, port, _path = _policy.split_url(url)
    if scheme == "file":
        return "file:"
    if scheme in ("http", "https") and host:
        shown = "[%s]" % host if ":" in host else host
        default = {"http": "80", "https": "443"}[scheme]
        return "%s://%s%s" % (scheme, shown, (":" + port) if port and port != default else "")
    return None


def coerce_storage(mapping, what):
    """Validate a {key: value} seed. Values become strings; lists and objects become JSON text."""
    out = {}
    for key, value in (mapping or {}).items():
        if not isinstance(key, str) or not key or len(key) > 200:
            raise ToolError("%s keys must be non-empty strings of at most 200 characters" % what)
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, separators=(",", ":"))
        elif value is None:
            raise ToolError("%s value for %r is null; use a string" % (what, key))
        else:
            value = str(value)
        if len(value) > 65536:
            raise ToolError("%s value for %r is longer than 64 KB" % (what, key))
        out[key] = value
    return out


def storage_script(origin, local, session):
    """JavaScript run before any page script. It seeds storage only on the page's own origin, so a redirect or an
    embedded frame from another origin never receives the values."""
    if not (local or session):
        return None
    guard = "location.protocol==='file:'" if origin == "file:" else "location.origin===%s" % json.dumps(origin or "")
    return ("(function(){try{if(!(%s)){return}var a=%s,b=%s,k;"
            "for(k in a){try{localStorage.setItem(k,a[k])}catch(e){}}"
            "for(k in b){try{sessionStorage.setItem(k,b[k])}catch(e){}}}catch(e){}})()") % (guard, json.dumps(local), json.dumps(session))


# ----------------------------------------------------------------------------
# request recorder

class Recorder(object):
    """Listens to one page: enforces the request policy and records console, network, and load facts."""

    def __init__(self, chrome, page, allow_remote=False, net_rules=None, offline=False, long_request_s=3.0):
        self.chrome, self.page = chrome, page
        self.long_request_s = long_request_s
        self.started = {}
        self.allow_remote = allow_remote
        self.rules = net_rules or []
        self.offline = offline
        self.reqs, self.inflight, self.hung, self.ours = {}, set(), set(), set()
        self.blocked, self.stubbed, self.mocked, self.failed = [], [], [], []
        self.blocked_full = set()
        self.forced = set()
        self.fault_injected = False
        self.hosts = Counter()
        self.console = {}
        self.internal_errors = []
        self.favicon_stubbed = False
        self.loaded = False
        self.last_activity = time.monotonic()
        for name, fn in (("Fetch.requestPaused", self._paused), ("Network.requestWillBeSent", self._req),
                         ("Network.responseReceived", self._resp), ("Network.loadingFinished", self._fin),
                         ("Network.loadingFailed", self._fail), ("Runtime.consoleAPICalled", self._console),
                         ("Runtime.exceptionThrown", self._exception), ("Log.entryAdded", self._log),
                         ("Page.loadEventFired", self._load), ("Page.javascriptDialogOpening", self._dialog)):
            page.on(name, fn)

    # request policy ---------------------------------------------------------
    def _fulfill(self, rid, sid, status, headers, body):
        self.chrome.fire("Fetch.fulfillRequest", {
            "requestId": rid, "responseCode": status,
            "responseHeaders": [{"name": k, "value": v} for k, v in headers],
            "body": base64.b64encode(body).decode("ascii")}, sid)

    def _fail_request(self, rid, sid, reason):
        self.chrome.fire("Fetch.failRequest", {"requestId": rid, "errorReason": reason}, sid)

    def _paused(self, msg):
        sid = msg.get("sessionId")
        rid = msg["params"]["requestId"]
        try:
            self._decide(msg["params"], sid)
        except Exception as error:  # fail closed: a request we cannot classify is not sent
            self.internal_errors.append(repr(error))
            self._fail_request(rid, sid, "BlockedByClient")

    def _decide(self, p, sid):
        req = p["request"]
        url, method, rid = req["url"], req["method"].upper(), p["requestId"]
        nid = p.get("networkId") or rid
        headers = req.get("headers") or {}
        rtype = p.get("resourceType", "")
        self.last_activity = time.monotonic()
        scheme, host, _port, path = _policy.split_url(url)
        if method == "OPTIONS" and scheme in ("http", "https"):
            self.ours.add(nid)
            return self._fulfill(rid, sid, 204, cors_headers(headers, True), b"")
        for rule in self.rules:
            if rule.matches(url, method):
                return self._apply_rule(rule, rid, sid, nid, url, method, headers)
        if path == "/favicon.ico" and method == "GET":
            self.favicon_stubbed = True
            self.ours.add(nid)
            return self._fulfill(rid, sid, 204, [], b"")
        if self.offline and rtype in ("XHR", "Fetch"):
            self.forced.add(url)
            self.fault_injected = True
            self.ours.add(nid)
            self.mocked.append({"method": method, "url": clip_text(_policy.redact_url(url), 120), "rule": "offline", "action": "fail"})
            return self._fail_request(rid, sid, "InternetDisconnected")
        action, reason, host2 = _policy.request_decision(url, method, self.allow_remote)
        if host2:
            self.hosts[host2] += 1
        if action == "allow":
            return self.chrome.fire("Fetch.continueRequest", {"requestId": rid}, sid)
        self.ours.add(nid)
        if action == "stub":
            self.stubbed.append({"method": method, "url": clip_text(_policy.redact_url(url), 120), "reason": reason})
            body = b"" if rtype == "Ping" else b'{"ok":true,"stubbed_by":"ui-measure"}'
            hdrs = cors_headers(headers) + ([("Content-Type", "application/json")] if body else [])
            return self._fulfill(rid, sid, 204 if not body else 200, hdrs, body)
        self.blocked_full.add(url)
        self.blocked.append({"url": clip_text(_policy.redact_url(url), 160), "host": host2 or "", "reason": reason})
        self._fail_request(rid, sid, "BlockedByClient")

    def _apply_rule(self, rule, rid, sid, nid, url, method, headers):
        rule.hits += 1
        self.mocked.append({"method": method, "url": clip_text(_policy.redact_url(url), 120), "rule": clip_text(rule.spec["match"], 60), "action": rule.action})
        if rule.action == "hang":
            self.hung.add(nid)
            return None
        self.ours.add(nid)
        if rule.action == "fail":
            self.forced.add(url)
            self.fault_injected = True
            return self._fail_request(rid, sid, rule.reason)
        spec = rule.spec
        status = int(spec.get("status", 200))
        if "json" in spec:
            body, ctype = json.dumps(spec["json"]).encode("utf-8"), "application/json"
        else:
            body, ctype = str(spec.get("body", "")).encode("utf-8"), spec.get("type", "text/plain")
        if status >= 400:
            self.forced.add(url)
            self.fault_injected = True
        self._fulfill(rid, sid, status, cors_headers(headers) + [("Content-Type", ctype)], body)

    # network bookkeeping ----------------------------------------------------
    def _req(self, msg):
        p = msg["params"]
        self.reqs[p["requestId"]] = {"url": p["request"]["url"], "method": p["request"]["method"], "type": p.get("type", "")}
        if p.get("type") not in ("EventSource", "WebSocket", "Ping"):  # streams and beacons never finish by design
            self.inflight.add(p["requestId"])
            self.started[p["requestId"]] = time.monotonic()
        self.last_activity = time.monotonic()

    def _resp(self, msg):
        p = msg["params"]
        r = self.reqs.get(p["requestId"])
        if r is not None:
            r["status"] = p["response"].get("status", 0)
        self.last_activity = time.monotonic()

    def _fin(self, msg):
        rid = msg["params"]["requestId"]
        self.inflight.discard(rid)
        r = self.reqs.get(rid)
        if r is not None and r.get("status", 0) >= 400:
            self._record_failed(r, "HTTP %d" % r["status"])
        self.last_activity = time.monotonic()

    def _fail(self, msg):
        p = msg["params"]
        rid = p["requestId"]
        self.inflight.discard(rid)
        r = self.reqs.get(rid)
        if r is not None and rid not in self.ours and not (p.get("canceled") and not p.get("blockedReason")):
            self._record_failed(r, p.get("errorText") or p.get("blockedReason") or "failed")
        self.last_activity = time.monotonic()

    def _record_failed(self, r, error):
        url = r["url"]
        self.failed.append({"url": clip_text(_policy.redact_url(url), 160), "path": clip_text(url.split("?")[0].split("#")[0], 120), "method": r["method"], "status": r.get("status"),
                            "error": clip_text(error, 80), "type": r.get("type", ""), "expected": url in self.forced})

    def pending(self):
        """Requests still in flight, ignoring hung ones and long polls older than long_request_s."""
        now = time.monotonic()
        return len([r for r in self.inflight - self.hung if now - self.started.get(r, now) < self.long_request_s])

    def quiet(self, quiet_ms):
        return self.pending() == 0 and (time.monotonic() - self.last_activity) * 1000.0 >= quiet_ms

    # console ------------------------------------------------------------------
    def _add_console(self, level, source, text, url=None):
        text = clip_text(text, 400)
        key = (level, source, text)
        entry = self.console.get(key)
        if entry is None:
            self.console[key] = {"level": level, "source": source, "text": text, "count": 1, "url": url}
        else:
            entry["count"] += 1

    @staticmethod
    def _arg(a):
        if "value" in a:
            return str(a["value"])
        return a.get("description") or a.get("unserializableValue") or a.get("type", "")

    def _console(self, msg):
        p = msg["params"]
        kind = p.get("type")
        if kind in ("error", "assert"):
            self._add_console("error", "console", " ".join(self._arg(a) for a in p.get("args", [])))
        elif kind == "warning":
            self._add_console("warning", "console", " ".join(self._arg(a) for a in p.get("args", [])))

    def _exception(self, msg):
        d = msg["params"]["exceptionDetails"]
        text = (d.get("exception") or {}).get("description") or d.get("text") or "uncaught exception"
        self._add_console("error", "exception", text.split("\n")[0])

    def _log(self, msg):
        e = msg["params"]["entry"]
        if e.get("level") in ("error", "warning"):
            self._add_console(e["level"], "log:" + (e.get("source") or ""), e.get("text", ""), url=e.get("url"))

    def _load(self, msg):
        self.loaded = True

    def _dialog(self, msg):
        p = msg["params"]
        self._add_console("warning", "dialog", "a %s dialog was dismissed: %s" % (p.get("type", "javascript"), p.get("message", "")))
        self.chrome.fire("Page.handleJavaScriptDialog", {"accept": False}, msg.get("sessionId"))

    # results ------------------------------------------------------------------
    def console_result(self):
        out = []
        for e in self.console.values():
            expected, cause = False, None
            url = e.get("url")
            if url and url in self.forced:
                expected, cause = True, "fault"
            elif url and url in self.blocked_full:
                expected, cause = True, "blocked"
            elif e["level"] == "error" and e["source"] == "console" and self.fault_injected:
                expected, cause = True, "fault"
            row = {"level": e["level"], "source": e["source"], "text": e["text"], "count": e["count"], "expected": expected}
            if cause:
                row["cause"] = cause
            out.append(row)
        return out[:60]

    def network_result(self):
        return {"requests": len(self.reqs), "failed": self.failed[:50], "blocked": self.blocked[:50], "stubbed": self.stubbed[:50],
                "mocked": self.mocked[:50], "hosts": dict(self.hosts), "favicon_stubbed": self.favicon_stubbed,
                "fault_injected": self.fault_injected, "hung": len(self.hung)}


# ----------------------------------------------------------------------------
# engine

class Engine(object):
    def __init__(self, cfg, allow_remote=False, scrollbars="hidden", focus=True, chrome_bin=None, collect_js=None):
        self.cfg = cfg
        self.allow_remote = allow_remote
        self.scrollbars = scrollbars
        self.focus = focus
        self.collect_js = collect_js or open(os.path.join(HERE, "collect.js"), encoding="utf-8").read()
        try:
            self.chrome = _cdp.Chrome(chrome_bin=chrome_bin, guard_network=not allow_remote, scrollbars="hidden" if scrollbars == "hidden" else "classic")
        except _cdp.CdpError as error:
            raise ToolError(str(error))
        self.version = None
        self._native_sb = None
        sheets = [ANIMATION_CSS]
        if scrollbars == "emulated":
            px = int(cfg.cap("scrollbar_emulated_px"))
            sheets.append(SCROLLBAR_CSS % (px, px))
        self._init_script = ("(function(){try{var t=%s,l=[];for(var i=0;i<t.length;i++){var s=new CSSStyleSheet();s.replaceSync(t[i]);l.push(s)}"
                             "document.adoptedStyleSheets=Array.prototype.slice.call(document.adoptedStyleSheets).concat(l)}catch(e){}})()") % json.dumps(sheets)

    def close(self):
        self.chrome.close()

    def browser_version(self):
        if self.version is None:
            try:
                self.version = self.chrome.version().get("product", "Chrome")
            except _cdp.CdpError:
                self.version = "Chrome"
        return self.version

    def scrollbar_info(self):
        """Mode plus the native scrollbar width of this Chrome, measured on a blank page."""
        info = {"mode": self.scrollbars}
        if self.scrollbars == "hidden":
            info["native_px"] = None
            return info
        if self._native_sb is None:
            page = self.chrome.new_page(isolated=True)
            try:
                page.call("Runtime.enable")
                self._native_sb = int(page.evaluate(
                    "(function(){var d=document.createElement('div');d.style.cssText='position:absolute;left:-9999px;top:0;width:100px;height:100px;overflow:scroll';"
                    "document.body.appendChild(d);var w=d.offsetWidth-d.clientWidth;d.remove();return w})()") or 0)
            finally:
                page.close()
        info["native_px"] = self._native_sb
        return info

    # waiting ------------------------------------------------------------------
    def _settle(self, page, rec, max_wait_s):
        cap = self.cfg
        self.chrome.run_until(lambda: rec.quiet(cap.cap("network_quiet_ms")), max_wait_s)
        try:
            page.evaluate("window.__uim.settle(%d,%d)" % (cap.cap("settle_max_ms"), cap.cap("settle_quiet_ms")), await_promise=True,
                          timeout=cap.cap("settle_max_ms") / 1000.0 + 10)
        except _cdp.JsError:
            pass

    def _has(self, page, spec):
        res = page.evaluate("window.__uim.has(%s)" % json.dumps(spec))
        if res.get("error"):
            raise ToolError("bad selector %r in the states spec: %s" % (spec, res["error"]))
        return bool(res.get("ok"))

    def _wait_for(self, page, spec, timeout_s):
        deadline = time.monotonic() + timeout_s
        while True:
            if self._has(page, spec):
                return True
            if time.monotonic() > deadline:
                return False
            self.chrome.wait(0.05)

    def _run_step(self, page, rec, step):
        wait = self.cfg.cap("step_settle_ms") / 1000.0
        if "click" in step:
            pt = page.evaluate("window.__uim.center(%s)" % json.dumps(step["click"]))
            if not pt:
                raise StepError("click target not found: %s" % clip_text(step["click"], 60))
            page.click(pt["x"], pt["y"])
        elif "type" in step:
            if not page.evaluate("window.__uim.focusSel(%s)" % json.dumps(step["type"])):
                raise StepError("type target not found or not focusable: %s" % clip_text(step["type"], 60))
            page.insert_text(str(step.get("text", "")))
        elif "press" in step:
            page.press_key(str(step["press"]))
        elif "wait" in step:
            self.chrome.wait(min(float(step["wait"]), 5000) / 1000.0)
        else:
            raise StepError("unknown step %s: use click, type, press, or wait" % clip_text(json.dumps(step), 60))
        self.chrome.wait(wait)
        self._settle(page, rec, 3)

    # one cell -------------------------------------------------------------------
    def capture(self, url, view, theme, state=None, ready_when=None, net_rules=None, pseudo=None, light=False, storage=None, squint=False):
        """Capture one cell. state is {id, steps, expect, offline} or None. light=True skips screenshots, names, and focus (used by init).
        storage is {"local": {k: v}, "session": {k: v}} to seed before any page script runs; values are never stored in evidence."""
        cfg = self.cfg
        t_start = time.monotonic()
        timing = {}
        offline = bool(state and state.get("offline"))
        rules = [NetRule(r) for r in (net_rules or (state or {}).get("net") or [])]
        page = self.chrome.new_page(isolated=True)
        try:
            rec = Recorder(self.chrome, page, self.allow_remote, rules, offline, float(cfg.cap("long_request_s")))
            for domain in ("Page", "Runtime", "Network", "Log"):
                page.call(domain + ".enable")
            page.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
            page.call("Network.setBypassServiceWorker", {"bypass": True})  # keep every request on the page's own, intercepted path
            page.call("Page.addScriptToEvaluateOnNewDocument", {"source": self._init_script})
            seed = storage or {}
            local_seed, session_seed = seed.get("local") or {}, seed.get("session") or {}
            seeder = storage_script(seed.get("origin") or origin_of(url), local_seed, session_seed)
            if seeder:
                page.call("Page.addScriptToEvaluateOnNewDocument", {"source": seeder})
            page.call("Emulation.setDeviceMetricsOverride", {"width": view["width"], "height": view["height"], "deviceScaleFactor": view["dpr"],
                                                             "mobile": False, "screenWidth": view["width"], "screenHeight": view["height"]})
            page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": theme},
                                                                  {"name": "prefers-reduced-motion", "value": "reduce"}]})
            page.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
            if view.get("touch"):
                page.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
            if url.startswith("https://") and _policy.is_loopback_host(_policy.split_url(url)[1]):
                page.call("Security.setIgnoreCertificateErrors", {"ignore": True})

            t = time.monotonic()
            res = page.call("Page.navigate", {"url": url}, timeout=cfg.cap("load_timeout_s") + 10)
            if res.get("errorText"):
                raise ToolError("could not load %s: %s" % (clip_text(url, 100), res["errorText"]))
            load_ok = self.chrome.run_until(lambda: rec.loaded, cfg.cap("load_timeout_s"))
            href = page.evaluate("location.href") or ""
            if href.startswith("chrome-error:"):
                raise ToolError("could not load %s (the browser showed an error page)" % clip_text(url, 100))
            page.evaluate(self.collect_js)
            present = {"local": [], "session": []}
            if local_seed or session_seed:
                found = page.evaluate("(function(n){var r={local:[],session:[]};"
                                      "try{n.local.forEach(function(k){if(localStorage.getItem(k)!==null){r.local.push(k)}})}catch(e){}"
                                      "try{n.session.forEach(function(k){if(sessionStorage.getItem(k)!==null){r.session.push(k)}})}catch(e){}"
                                      "return r})(%s)" % json.dumps({"local": sorted(local_seed), "session": sorted(session_seed)})) or {}
                present = {"local": found.get("local", []), "session": found.get("session", [])}
            self._settle(page, rec, cfg.cap("ready_timeout_s"))
            ready, ready_reason = None, None
            if ready_when:
                ready = self._wait_for(page, ready_when, cfg.cap("ready_timeout_s"))
                if not ready:
                    ready_reason = "ready_when %s never became visible within %ss" % (clip_text(ready_when, 60), cfg.cap("ready_timeout_s"))
            timing["load"] = int((time.monotonic() - t) * 1000)

            t = time.monotonic()
            state_info = {"id": (state or {}).get("id", "default"), "confirmed": True, "ready": ready, "ready_reason": ready_reason,
                          "expect": (state or {}).get("expect"), "steps": len((state or {}).get("steps") or [])}
            if offline:
                page.call("Network.emulateNetworkConditions", {"offline": True, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
                self.chrome.wait(0.05)
            try:
                for step in (state or {}).get("steps") or []:
                    self._run_step(page, rec, step)
            except StepError as error:
                state_info["confirmed"], state_info["reason"] = False, str(error)
            if state_info["confirmed"] and state_info["expect"]:
                wait_s = float((state or {}).get("timeout") or cfg.cap("expect_timeout_s"))
                if not self._wait_for(page, state_info["expect"], wait_s):
                    state_info["confirmed"] = False
                    state_info["reason"] = "expected %s was not visible after %ss" % (clip_text(state_info["expect"], 80), _rules.num(wait_s))
            if state and not state.get("expect") and not state.get("steps") and not state.get("net") and not offline:
                if state_info["id"] in ("default", "ready"):
                    state_info["reason"] = "no expect given; the state is assumed rendered"
                else:
                    state_info["confirmed"] = False
                    state_info["reason"] = "no expect, steps, or net rule: nothing distinguishes this state from the default page"
            if state and (state.get("expect") or state.get("steps") or state.get("net") or offline):
                self._settle(page, rec, 2)
            timing["state"] = int((time.monotonic() - t) * 1000)

            t = time.monotonic()
            cap_h = cfg.cap("full_page_cap_px")
            squint_png = None
            if light:
                png = None
                width_px = height_px = 0
            else:
                png = page.screenshot()
                width_px, height_px = _png.read_size(png)
                if squint:
                    scale = min(1.0, cfg.cap("squint_width_px") / float(view["width"]))
                    small = page.screenshot(clip={"x": 0, "y": 0, "width": view["width"], "height": view["height"], "scale": scale})
                    squint_png = _png.squint(small, int(cfg.cap("squint_blur_px")))
            timing["screenshots"] = int((time.monotonic() - t) * 1000)

            t = time.monotonic()
            raw = page.evaluate("window.__uim.collect(%s)" % json.dumps({"maxNodes": cfg.cap("max_nodes"), "ignore": cfg.cap("ignore_selector")}), timeout=90)
            timing["collect"] = int((time.monotonic() - t) * 1000)
            tokens = page.evaluate("window.__uim.tokens()")

            t = time.monotonic()
            ax = {} if light else self._ax(page, len(raw.get("controls") or []))
            timing["ax"] = int((time.monotonic() - t) * 1000)

            t = time.monotonic()
            focus = self._focus_walk(page) if (self.focus and not light) else None
            timing["focus"] = int((time.monotonic() - t) * 1000)
            # Freeze what the page logged and requested now: the full-page screenshot and the pseudo-states below use DevTools
            # features that resize the viewport or add their own noise, so they run after everything is measured.
            console, network = rec.console_result(), rec.network_result()

            t = time.monotonic()
            if light:
                full, full_h, content_h = None, 0, 0
            else:
                lm = page.call("Page.getLayoutMetrics")
                content_h = int(math.ceil((lm.get("cssContentSize") or lm.get("contentSize") or {}).get("height", view["height"])))
                if content_h > view["height"] + 1:
                    full_h = min(content_h, cap_h)
                    full = page.screenshot(clip={"x": 0, "y": 0, "width": view["width"], "height": full_h, "scale": 1}, beyond=True)
                    self.chrome.wait(0.05)
                else:
                    full_h, full = content_h, png
            timing["screenshots"] += int((time.monotonic() - t) * 1000)

            pseudo_results = None
            if pseudo:
                t = time.monotonic()
                pseudo_results = self._pseudo(page, pseudo, view)
                timing["pseudo"] = int((time.monotonic() - t) * 1000)
            self.chrome.wait(0.02)
            timing["total"] = int((time.monotonic() - t_start) * 1000)
            return {"raw": raw, "png": png, "png_size": [width_px, height_px], "full": full, "squint": squint_png, "full_height": full_h, "page_height": content_h,
                    "capped": content_h > cap_h, "ax": ax, "focus": focus, "tokens": tokens, "state": state_info, "pseudo": pseudo_results,
                    "console": console, "network": network, "load_ok": load_ok, "timing": timing,
                    "storage": {"keys": {"local": sorted(local_seed), "session": sorted(session_seed)}, "present": present},
                    "internal_errors": rec.internal_errors + list(self.chrome.handler_errors[:5])}
        finally:
            page.close()

    # accessibility, focus, pseudo-states ---------------------------------------------
    def _ax(self, page, count):
        if not count:
            return {}
        out = {}
        try:
            page.call("Accessibility.enable")
            arr = page.evaluate("window.__uim._ctl", by_value=False)
            props = page.call("Runtime.getProperties", {"objectId": arr["objectId"], "ownProperties": True}, timeout=30)["result"]
            for prop in props:
                if not prop["name"].isdigit() or int(prop["name"]) >= self.cfg.cap("max_ax_controls"):
                    continue
                oid = (prop.get("value") or {}).get("objectId")
                if not oid:
                    continue
                nodes = page.call("Accessibility.getPartialAXTree", {"objectId": oid, "fetchRelatives": False}, timeout=10).get("nodes") or []
                if nodes:
                    n = nodes[0]
                    via = ""
                    for src in (n.get("name") or {}).get("sources") or []:
                        if (src.get("value") or {}).get("value") and not src.get("superseded"):
                            via = src.get("type") or ""
                            break
                    out[int(prop["name"])] = {"name": (n.get("name") or {}).get("value", ""), "role": (n.get("role") or {}).get("value", ""),
                                              "ignored": bool(n.get("ignored")), "via": via}
        except (_cdp.CdpError, KeyError):
            return out
        return out

    def _focus_walk(self, page):
        limit = self.cfg.cap("max_focus_stops")
        total = page.evaluate("window.__uim.focusables()")
        page.evaluate("window.scrollTo(0, 0)")
        if total and page.evaluate("window.__uim.focusLast()"):
            for _ in range(4):
                page.press_key("Tab")
                if page.evaluate("window.__uim.focusNow()") is None:
                    break
        stops, seen = [], set()
        wrapped = truncated = False
        presses = 0
        while presses < limit * 2 + 12:
            page.press_key("Tab")
            presses += 1
            info = page.evaluate("window.__uim.focusNow()")
            if info is None:
                wrapped = True
                break
            if info.get("skip"):
                continue
            key = (info["s"], int(info["r"][0]), int(info["r"][1]))
            if key in seen:
                wrapped = True
                break
            seen.add(key)
            stops.append(info)
            if len(stops) >= limit:
                truncated = True
                break
        page.evaluate("(function(){var a=document.activeElement;if(a&&a.blur){a.blur()}window.scrollTo(0,0)})()")
        return {"stops": stops, "total": total, "wrapped": wrapped, "truncated": truncated, "presses": presses}

    def _pseudo(self, page, entries, view):
        results = []
        page.call("DOM.enable")
        page.call("CSS.enable")
        pad = 12
        for entry in entries:
            sel = entry.get("sel")
            if not isinstance(sel, str) or not sel:
                raise ToolError("every pseudo entry needs a \"sel\" selector")
            for pstate in entry.get("states") or ["hover", "focus-visible", "active"]:
                if pstate not in PSEUDO_STATES:
                    raise ToolError("pseudo state %r is not one of %s" % (pstate, ", ".join(PSEUDO_STATES)))
                base = page.evaluate("window.__uim.snap(%s)" % json.dumps(sel))
                if base.get("error"):
                    raise ToolError("bad selector %r in pseudo: %s" % (sel, base["error"]))
                row = {"sel": sel, "state": pstate, "found": base.get("found", 0)}
                if not base.get("found"):
                    results.append(row)
                    continue
                doc = page.call("DOM.getDocument", {"depth": 0})
                nid = page.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": sel})["nodeId"]
                r = base["r"]
                x0, y0 = max(0, int(r[0]) - pad), max(0, int(r[1]) - pad)
                clip = {"x": x0, "y": y0, "width": max(1, min(int(r[2]) + 2 * pad, view["width"] - x0)), "height": max(1, min(int(r[3]) + 2 * pad, 600))}
                before = page.screenshot(clip=clip, beyond=True)
                page.call("CSS.forcePseudoState", {"nodeId": nid, "forcedPseudoClasses": [pstate]})
                self.chrome.wait(0.03)
                forced = page.evaluate("window.__uim.snap(%s)" % json.dumps(sel))
                after = page.screenshot(clip=clip, beyond=True)
                page.call("CSS.forcePseudoState", {"nodeId": nid, "forcedPseudoClasses": []})
                row.update({"base": base, "forced": forced, "png": after, "png_before": before,
                            "changed": hashlib.sha256(before).hexdigest() != hashlib.sha256(after).hexdigest()})
                results.append(row)
        return results

    # contact sheet -----------------------------------------------------------------------
    def contact_sheet(self, cells, scale, gap):
        """Render an HTML grid of the captured PNGs in this Chrome session. cells: [{label, png, width, ok}]."""
        if not cells:
            return None
        cols = sorted(set(c["width"] for c in cells))
        rows = []
        for c in cells:
            if c["row"] not in rows:
                rows.append(c["row"])
        col_w = dict((w, max(40, int(w * scale))) for w in cols)
        total_w = sum(col_w.values()) + gap * (len(cols) + 1) + 140
        parts = ["<!doctype html><meta charset=utf-8><style>body{margin:0;padding:%dpx;background:#f4f4f5;font:12px/1.3 system-ui,sans-serif;color:#18181b}"
                 "table{border-collapse:separate;border-spacing:%dpx}td,th{vertical-align:top;text-align:left;padding:0}"
                 "th.row{width:128px;padding-right:8px;font-weight:600}.cell{background:#fff;border:1px solid #d4d4d8}"
                 ".cell img{display:block;width:100%%;height:auto}.cap{padding:4px 6px;border-top:1px solid #d4d4d8;font-size:11px}"
                 ".bad{background:#fee2e2;color:#991b1b}.ok{background:#f4f4f5}</style><table><tr><th></th>" % (gap, gap)]
        for w in cols:
            parts.append("<th>%d px</th>" % w)
        parts.append("</tr>")
        by_key = dict(((c["row"], c["width"]), c) for c in cells)
        for row in rows:
            parts.append("<tr><th class=row>%s</th>" % html.escape(row))
            for w in cols:
                c = by_key.get((row, w))
                if c is None:
                    parts.append("<td></td>")
                    continue
                uri = "data:image/png;base64," + base64.b64encode(c["png"]).decode("ascii")
                parts.append("<td style='width:%dpx'><div class=cell><img src='%s'><div class='cap %s'>%s</div></div></td>" % (
                    col_w[w], uri, "ok" if c["ok"] else "bad", html.escape(c["label"])))
            parts.append("</tr>")
        parts.append("</table>")
        page = self.chrome.new_page(isolated=True)
        try:
            for domain in ("Page", "Runtime"):
                page.call(domain + ".enable")
            page.call("Emulation.setDeviceMetricsOverride", {"width": total_w, "height": 800, "deviceScaleFactor": 1, "mobile": False})
            frame = page.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
            page.call("Page.setDocumentContent", {"frameId": frame, "html": "".join(parts)}, timeout=60)
            page.evaluate("Promise.all(Array.prototype.map.call(document.images,function(i){return i.decode().catch(function(){})}))", await_promise=True, timeout=60)
            lm = page.call("Page.getLayoutMetrics")
            size = lm.get("cssContentSize") or lm.get("contentSize")
            height = int(math.ceil(size["height"]))
            return page.screenshot(clip={"x": 0, "y": 0, "width": total_w, "height": min(height, 16000), "scale": 1}, beyond=True, timeout=60)
        finally:
            page.close()
