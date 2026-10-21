"""_cdp.py: launch headless Chrome and drive it over a private DevTools pipe.

Trust boundary: Chrome runs locally as a child process. Commands travel over two
inherited pipe descriptors (3 and 4), so there is no debugging port and nothing
else can attach. By default the browser is started with a dead proxy so any
request that is not loopback fails inside the browser itself, in addition to the
request policy that ui_measure.py enforces through the Fetch domain. This module
opens no sockets. The throwaway Chrome profile lives in the OS temp directory and
is deleted when the browser closes.

Compatible with Python 3.9. macOS and Linux only (it uses pass_fds and /bin/sh).
"""
import base64
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time

MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_CANDIDATES = (
    MAC_CHROME,
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")

BASE_FLAGS = (
    "--headless=new",
    "--remote-debugging-pipe",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-extensions",
    "--metrics-recording-only",
    "--mute-audio",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-search-engine-choice-screen",
    "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication",
    "--block-new-web-contents",
)

HIDE_SCROLLBARS = "--hide-scrollbars"

# A proxy that nothing listens on. Chrome never proxies loopback, so local pages
# still load, while every other host fails to connect.
DEAD_PROXY = "--proxy-server=http://127.0.0.1:1"

KEYS = {
    "Tab": (9, None),
    "Enter": (13, "\r"),
    "Escape": (27, None),
    "Space": (32, " "),
    "ArrowDown": (40, None),
    "ArrowUp": (38, None),
    "ArrowLeft": (37, None),
    "ArrowRight": (39, None),
    "Backspace": (8, None),
}


class CdpError(Exception):
    pass


class ChromeGone(CdpError):
    pass


class CdpTimeout(CdpError):
    pass


class JsError(CdpError):
    pass


def find_chrome():
    """Return the Chrome binary: CHROME_BIN first, then common install paths."""
    override = os.getenv("CHROME_BIN")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        raise CdpError("CHROME_BIN is set but is not an executable file: %s" % override)
    for path in CHROME_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise CdpError("Chrome was not found. Install Google Chrome or set CHROME_BIN to its executable.")


class Chrome:
    """One headless Chrome process plus the event loop that talks to it."""

    def __init__(self, chrome_bin=None, guard_network=True, scrollbars="hidden", extra_flags=()):
        self.chrome_bin = chrome_bin or find_chrome()
        self._profile = tempfile.TemporaryDirectory(prefix="ui-measure-chrome-")
        self._next_id = 0
        self._buf = bytearray()
        self._queue = []
        self._responses = {}
        self._fire = set()
        self._handlers = {}
        self.handler_errors = []
        self._closed = False
        in_r, in_w = os.pipe()
        out_r, out_w = os.pipe()
        flags = list(BASE_FLAGS)
        if scrollbars != "classic":
            flags.append(HIDE_SCROLLBARS)
        if guard_network:
            flags.append(DEAD_PROXY)
        flags.extend(extra_flags)
        flags.append("--user-data-dir=" + self._profile.name)
        flags.append("about:blank")
        script = 'exec "$0" "$@" 3<&%d 4>&%d' % (in_r, out_w)
        try:
            self.proc = subprocess.Popen(
                ["/bin/sh", "-c", script, self.chrome_bin] + flags,
                pass_fds=(in_r, out_w),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            for fd in (in_r, in_w, out_r, out_w):
                os.close(fd)
            self._profile.cleanup()
            raise
        os.close(in_r)
        os.close(out_w)
        self._w, self._r = in_w, out_r
        os.set_blocking(self._w, False)
        os.set_blocking(self._r, False)

    # transport -------------------------------------------------------------

    def _fill(self, timeout):
        """Read whatever Chrome has written and split it into messages. Never dispatches."""
        try:
            ready, _, _ = select.select([self._r], [], [], timeout)
        except (OSError, ValueError):
            raise ChromeGone("the DevTools pipe is closed")
        if not ready:
            return
        try:
            data = os.read(self._r, 1 << 20)
        except BlockingIOError:
            return
        except OSError:
            raise ChromeGone("the DevTools pipe is closed")
        if not data:
            raise ChromeGone("Chrome exited")
        self._buf += data
        while True:
            end = self._buf.find(b"\0")
            if end < 0:
                break
            raw = bytes(self._buf[:end])
            del self._buf[:end + 1]
            if not raw:
                continue
            msg = json.loads(raw.decode("utf-8", "replace"))
            if "id" in msg:
                if msg["id"] in self._fire:
                    self._fire.discard(msg["id"])
                    if "error" in msg:
                        self.handler_errors.append("fire: %s" % msg["error"].get("message", msg["error"]))
                else:
                    self._responses[msg["id"]] = msg
            else:
                self._queue.append(msg)

    def _write(self, payload):
        view = memoryview(payload)
        offset = 0
        deadline = time.monotonic() + 60
        while offset < len(view):
            if time.monotonic() > deadline:
                raise CdpTimeout("writing to Chrome")
            try:
                _, writable, _ = select.select([self._r], [self._w], [], 0.2)
            except (OSError, ValueError):
                raise ChromeGone("the DevTools pipe is closed")
            if writable:
                try:
                    offset += os.write(self._w, view[offset:offset + 65536])
                except BlockingIOError:
                    pass
                except (BrokenPipeError, OSError):
                    raise ChromeGone("Chrome exited")
            self._fill(0)

    def send(self, method, params=None, session_id=None):
        self._next_id += 1
        message = {"id": self._next_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self._write(json.dumps(message).encode("utf-8") + b"\0")
        return self._next_id

    def fire(self, method, params=None, session_id=None):
        """Send a command whose reply nobody waits for (answering an intercepted request)."""
        self._next_id += 1
        self._fire.add(self._next_id)
        message = {"id": self._next_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self._write(json.dumps(message).encode("utf-8") + b"\0")

    def _dispatch(self, msg):
        for sid, fn in list(self._handlers.get(msg.get("method"), ())):
            if sid is None or sid == msg.get("sessionId"):
                try:
                    fn(msg)
                except ChromeGone:
                    raise
                except Exception as error:  # a broken handler must not stop the transport
                    self.handler_errors.append("%s: %r" % (msg.get("method"), error))

    def pump(self, timeout=0.05):
        """Read from Chrome for up to timeout seconds and dispatch queued events."""
        self._fill(timeout)
        while self._queue:
            msg = self._queue.pop(0)
            self._dispatch(msg)

    def call(self, method, params=None, session_id=None, timeout=30.0):
        msg_id = self.send(method, params, session_id)
        deadline = time.monotonic() + timeout
        while True:
            if msg_id in self._responses:
                resp = self._responses.pop(msg_id)
                if "error" in resp:
                    raise CdpError("%s: %s" % (method, resp["error"].get("message", resp["error"])))
                return resp.get("result", {})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpTimeout("%s timed out after %.0fs" % (method, timeout))
            self.pump(min(remaining, 0.25))

    def on(self, method, fn, session_id=None):
        self._handlers.setdefault(method, []).append((session_id, fn))

    def drop_handlers(self, session_id):
        for method in list(self._handlers):
            self._handlers[method] = [(sid, fn) for sid, fn in self._handlers[method] if sid != session_id]

    def run_until(self, condition, timeout):
        """Pump events until condition() is true. Returns False when the timeout passes first."""
        deadline = time.monotonic() + timeout
        while not condition():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.pump(min(remaining, 0.05))
        return True

    def wait(self, seconds):
        self.run_until(lambda: False, seconds)

    # lifecycle ---------------------------------------------------------------

    def version(self):
        return self.call("Browser.getVersion")

    def new_page(self, isolated=True):
        context_id = None
        if isolated:
            context_id = self.call("Target.createBrowserContext", {"disposeOnDetach": False})["browserContextId"]
            # A page may not write files outside the evidence folder: downloads are refused.
            self.call("Browser.setDownloadBehavior", {"behavior": "deny", "browserContextId": context_id})
        params = {"url": "about:blank"}
        if context_id:
            params["browserContextId"] = context_id
        target_id = self.call("Target.createTarget", params)["targetId"]
        session_id = self.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})["sessionId"]
        return Page(self, session_id, target_id, context_id)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.call("Browser.close", timeout=2)
        except Exception:
            pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass
        for fd in (self._w, self._r):
            try:
                os.close(fd)
            except OSError:
                pass
        for _ in range(10):
            try:
                self._profile.cleanup()
                break
            except OSError:
                time.sleep(0.1)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Page:
    """A browser tab attached through a flattened session."""

    def __init__(self, chrome, session_id, target_id, context_id):
        self.chrome = chrome
        self.sid = session_id
        self.target_id = target_id
        self.context_id = context_id

    def call(self, method, params=None, timeout=30.0):
        return self.chrome.call(method, params, self.sid, timeout)

    def on(self, method, fn):
        self.chrome.on(method, fn, self.sid)

    def evaluate(self, expression, await_promise=False, timeout=30.0, by_value=True):
        res = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": by_value, "awaitPromise": await_promise},
            timeout,
        )
        if "exceptionDetails" in res:
            details = res["exceptionDetails"]
            text = (details.get("exception") or {}).get("description") or details.get("text") or "script error"
            raise JsError(text.split("\n")[0][:300])
        result = res.get("result", {})
        return result.get("value") if by_value else result

    def fire(self, method, params=None):
        self.chrome.fire(method, params, self.sid)

    def screenshot(self, clip=None, beyond=False, timeout=30.0):
        params = {"format": "png"}
        if beyond:
            params["captureBeyondViewport"] = True
        if clip:
            params["clip"] = {
                "x": clip["x"], "y": clip["y"], "width": clip["width"], "height": clip["height"],
                "scale": clip.get("scale", 1),
            }
        return base64.b64decode(self.call("Page.captureScreenshot", params, timeout)["data"])

    def press_key(self, name):
        code, text = KEYS.get(name, (0, None))
        down = {"type": "keyDown" if text else "rawKeyDown", "key": name, "code": name,
                "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
        if text:
            down["text"] = text
        self.call("Input.dispatchKeyEvent", down)
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": name, "code": name,
                                             "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code})

    def click(self, x, y):
        self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    def insert_text(self, text):
        self.call("Input.insertText", {"text": text})

    def close(self):
        self.chrome.drop_handlers(self.sid)
        try:
            self.chrome.call("Target.closeTarget", {"targetId": self.target_id}, timeout=5)
        except CdpError:
            pass
        if self.context_id:
            try:
                self.chrome.call("Target.disposeBrowserContext", {"browserContextId": self.context_id}, timeout=5)
            except CdpError:
                pass
