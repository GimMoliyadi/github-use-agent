"""Nonblocking single-owner lock on POSIX and native Windows."""

import errno
import os
from pathlib import Path


class ProcessLock:
    def __init__(self, path: Path):
        self.handle = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if path.stat().st_size == 0:
                    self.handle.write(b"\0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            if error.errno in {errno.EACCES, errno.EAGAIN} or getattr(
                error, "winerror", None
            ) in {33, 36}:
                raise ValueError("A research service already owns this data directory.") from None
            raise

    def close(self):
        if self.handle.closed:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
