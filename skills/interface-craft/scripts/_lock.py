"""_lock.py: a lock on the evidence root, so parallel agents can capture into one .ui-evidence/.

Two things must be unique across the whole evidence root: the run folder number (NNN-phase) and the evidence ids (E01, E02, ...).
Both are handed out under one exclusive lock held for a few milliseconds, never while Chrome is running.

  * Primary: fcntl.flock on <root>/.lock. The kernel drops the lock when a process dies, so it cannot go stale.
  * Fallback, where fcntl is missing: an atomic mkdir of <root>/.lock.d with a stale-lock check (the owner's pid is gone, or the lock is
    older than stale_s). Recovery removes the dead lock and tries again.

Ids come from a counter file, <root>/.ids, and from the highest E##.json found on disk, whichever is higher, so a root made before the
counter existed keeps counting from where it was. A caller can reserve a block of consecutive ids for one run, which keeps a run's ids
together when several runs share the root.

Compatible with Python 3.9. Standard library only.
"""
import os
import re
import shutil
import time

try:
    import fcntl
except ImportError:  # not on macOS or Linux: the mkdir lock below still works
    fcntl = None

LOCK_FILE = ".lock"
LOCK_DIR = ".lock.d"
IDS_FILE = ".ids"


class LockTimeout(Exception):
    """The evidence root stayed locked past the timeout."""


def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class EvidenceLock(object):
    """Exclusive lock on an evidence root. Use as a context manager; it creates the root when it is missing."""

    def __init__(self, root, timeout_s=30.0, stale_s=120.0, use_flock=None):
        self.root = root
        self.timeout_s = timeout_s
        self.stale_s = stale_s
        self.use_flock = (fcntl is not None) if use_flock is None else bool(use_flock and fcntl is not None)
        self._fd = None
        self._held = None

    # flock --------------------------------------------------------------------
    def _acquire_flock(self):
        os.makedirs(self.root, exist_ok=True)
        fd = os.open(os.path.join(self.root, LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return
            except (IOError, OSError):
                if time.monotonic() > deadline:
                    os.close(fd)
                    raise LockTimeout("the evidence folder %s stayed locked for %ds; another capture may be stuck" % (self.root, self.timeout_s))
                time.sleep(0.01)

    # mkdir --------------------------------------------------------------------
    def _owner_file(self):
        return os.path.join(self.root, LOCK_DIR, "owner")

    def _stale(self):
        """True when the mkdir lock is held by a process that is gone, or has been held longer than stale_s."""
        try:
            with open(self._owner_file(), encoding="utf-8") as handle:
                pid_text, _sep, _t = handle.read().partition(" ")
            pid = int(pid_text)
        except (OSError, ValueError):
            pid = -1  # no readable owner: a lock whose owner never wrote (or lost) its record is older than any live holder
        try:
            age = time.time() - os.path.getmtime(os.path.join(self.root, LOCK_DIR))
        except OSError:
            return True
        if pid == -1:
            return age > 2.0
        return (not _pid_alive(pid)) or age > self.stale_s

    def _acquire_mkdir(self):
        os.makedirs(self.root, exist_ok=True)
        path = os.path.join(self.root, LOCK_DIR)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                os.mkdir(path)
                with open(self._owner_file(), "w", encoding="utf-8") as handle:
                    handle.write("%d %d" % (os.getpid(), int(time.time())))
                self._held = path
                return
            except FileExistsError:
                if self._stale():
                    shutil.rmtree(path, ignore_errors=True)  # recover from a lock its owner never released
                    continue
                if time.monotonic() > deadline:
                    raise LockTimeout("the evidence folder %s stayed locked for %ds; another capture may be stuck" % (self.root, self.timeout_s))
                time.sleep(0.01)

    # context manager -------------------------------------------------------------
    def __enter__(self):
        if self.use_flock:
            self._acquire_flock()
        else:
            self._acquire_mkdir()
        return self

    def __exit__(self, _type, _value, _tb):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
        if self._held is not None:
            shutil.rmtree(self._held, ignore_errors=True)
            self._held = None
        return False


# ----------------------------------------------------------------------------
# what is handed out under the lock

def disk_top_id(root):
    """The highest evidence number on disk (E##.json inside any run folder), or 0."""
    top = 0
    if os.path.isdir(root):
        for d in os.listdir(root):
            full = os.path.join(root, d)
            if os.path.isdir(full) and re.match(r"^\d{3}-", d):
                for f in os.listdir(full):
                    m = re.match(r"^E(\d+)\.json$", f)
                    if m:
                        top = max(top, int(m.group(1)))
    return top


def _read_counter(root):
    try:
        with open(os.path.join(root, IDS_FILE), encoding="utf-8") as handle:
            return int(handle.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_counter(root, value):
    tmp = os.path.join(root, IDS_FILE + ".%d.tmp" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("%d\n" % value)
    os.replace(tmp, os.path.join(root, IDS_FILE))


def allocate_ids(root, count=1, **lock_args):
    """Reserve `count` consecutive evidence numbers and return the first. No other process gets any of them."""
    with EvidenceLock(root, **lock_args):
        top = max(disk_top_id(root), _read_counter(root))
        _write_counter(root, top + count)
    return top + 1


def allocate_run(root, phase, label_slug, **lock_args):
    """Create the next run folder, NNN-phase[-label], and return its name. The number is unique across the root, even for parallel runs."""
    with EvidenceLock(root, **lock_args):
        numbers = [int(m.group(1)) for d in os.listdir(root) for m in [re.match(r"^(\d{3})-", d)] if m and os.path.isdir(os.path.join(root, d))]
        name = "%03d-%s" % (max(numbers or [0]) + 1, phase) + ("-" + label_slug if label_slug else "")
        os.mkdir(os.path.join(root, name))
    return name
