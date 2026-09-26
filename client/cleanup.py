"""
cleanup.py
----------
Phase 5: No-trace / cleanup utilities.

Responsibilities:
  1. Best-effort zeroing of sensitive byte buffers (private keys, derived
     AES keys) once they're no longer needed, rather than just letting
     Python's garbage collector handle it eventually.
  2. Managing a scoped temp directory for anything the app needs to write
     during a session, ensuring it's deleted on exit.
  3. Reacting to USB removal by tearing down the session immediately.

Design notes / honest limitations:
  - Python strings and `bytes` are immutable, so a `bytes` object holding
    key material CANNOT be zeroed in place -- a new bytes object copy may
    still exist in memory until garbage collected. To actually get
    zeroable buffers, sensitive material should be kept in a
    `bytearray` (mutable) wherever possible, which this module assumes.
  - This is "best-effort" hardening, not a hard guarantee. True memory
    scrubbing that defeats a sophisticated forensic memory dump is a much
    deeper OS-level problem (paging to disk, swap files, etc.) that a
    portable Python app cannot fully solve. This module significantly
    reduces the window and casual exposure, which is the realistic goal.
  - USB removal detection is OS-specific. This module includes a
    Windows-focused polling approach (checking if the drive letter/path
    the app was launched from still exists), since the stated target
    platform is Windows.
"""

import os
import shutil
import tempfile
import threading
import time
from typing import Callable, Optional


def zero_bytearray(buffer: bytearray) -> None:
    """
    Overwrite a mutable bytearray's contents with zeros in place.
    Use this for any key material held as a bytearray once it's no
    longer needed (e.g. after a session ends or before the app exits).
    """
    for i in range(len(buffer)):
        buffer[i] = 0


class SessionTempDir:
    """
    A temp directory scoped to one app session. Anything written here
    is guaranteed to be removed when the session ends (via `cleanup()`
    or automatically as a context manager).

    Usage:
        with SessionTempDir() as tmp:
            path = tmp.path_for("scratch_file.bin")
            # ... use path ...
        # directory and all contents are gone here
    """

    def __init__(self, prefix: str = "usbmsg_"):
        self._dir = tempfile.mkdtemp(prefix=prefix)

    @property
    def path(self) -> str:
        return self._dir

    def path_for(self, filename: str) -> str:
        return os.path.join(self._dir, filename)

    def cleanup(self) -> None:
        """Remove the temp directory and everything in it."""
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False  # don't suppress exceptions


class USBWatchdog:
    """
    Polls whether the path the app was launched from is still present.
    If the USB is physically removed, `on_removed` is called so the
    application can immediately wipe session state and shut down.

    This uses simple polling rather than OS-level device-change events
    to keep the implementation portable and dependency-free; the poll
    interval is short enough to react quickly without noticeable CPU cost.
    """

    def __init__(self, watched_path: str, on_removed: Callable[[], None], poll_interval: float = 1.0):
        self.watched_path = watched_path
        self.on_removed = on_removed
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            if not os.path.exists(self.watched_path):
                self.on_removed()
                return
            time.sleep(self.poll_interval)


class SessionCleanupManager:
    """
    Central place the main app registers sensitive buffers and temp
    resources with, so a single `wipe_all()` call at exit (or on USB
    removal) tears everything down consistently.
    """

    def __init__(self):
        self._sensitive_buffers: list[bytearray] = []
        self._temp_dirs: list[SessionTempDir] = []

    def register_sensitive_buffer(self, buffer: bytearray) -> None:
        self._sensitive_buffers.append(buffer)

    def register_temp_dir(self, temp_dir: SessionTempDir) -> None:
        self._temp_dirs.append(temp_dir)

    def wipe_all(self) -> None:
        for buf in self._sensitive_buffers:
            zero_bytearray(buf)
        self._sensitive_buffers.clear()

        for temp_dir in self._temp_dirs:
            temp_dir.cleanup()
        self._temp_dirs.clear()


# ---- Manual test / demo ------------------------------------------------
if __name__ == "__main__":
    print("=== Phase 5 Demo: Cleanup Utilities ===\n")

    print("[1] Testing bytearray zeroing...")
    secret = bytearray(b"super-secret-private-key-bytes")
    print(f"    Before: {bytes(secret)}")
    zero_bytearray(secret)
    print(f"    After:  {bytes(secret)}")
    assert all(b == 0 for b in secret)
    print("    Zeroing confirmed.")

    print("\n[2] Testing scoped temp directory...")
    with SessionTempDir() as tmp:
        test_file = tmp.path_for("test.txt")
        with open(test_file, "w") as f:
            f.write("temporary session data")
        print(f"    Created: {test_file}, exists={os.path.exists(test_file)}")
    print(f"    After context exit, exists={os.path.exists(test_file)}")
    assert not os.path.exists(test_file)

    print("\n[3] Testing SessionCleanupManager (combined wipe)...")
    manager = SessionCleanupManager()
    key1 = bytearray(b"private-key-material-1")
    key2 = bytearray(b"private-key-material-2")
    manager.register_sensitive_buffer(key1)
    manager.register_sensitive_buffer(key2)

    temp = SessionTempDir()
    scratch = temp.path_for("scratch.bin")
    with open(scratch, "wb") as f:
        f.write(b"some session scratch data")
    manager.register_temp_dir(temp)

    print(f"    Before wipe: key1={bytes(key1)[:10]}..., scratch exists={os.path.exists(scratch)}")
    manager.wipe_all()
    print(f"    After wipe:  key1={bytes(key1)[:10]}..., scratch exists={os.path.exists(scratch)}")
    assert all(b == 0 for b in key1)
    assert not os.path.exists(scratch)

    print("\nAll Phase 5 cleanup checks passed.")
