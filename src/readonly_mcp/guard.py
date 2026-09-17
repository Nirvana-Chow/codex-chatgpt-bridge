"""Descriptor-relative, no-follow reads; there is deliberately no write API."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
import stat


class Denied(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


FORBIDDEN = (".env*", ".git", ".ssh", ".aws", ".gnupg", "*.pem", "*.key",
             "*.p12", "*.pfx", "*.keychain*", "id_rsa*", "id_ed25519*",
             "credentials*", "secrets*", "auth.json", "cookies*", ".netrc")
MAX_FILE_BYTES = 20 * 1024 * 1024


def parts_for(relative: str) -> tuple[str, ...]:
    if not relative or relative.startswith("/") or any(x in relative for x in ("\\", "\0", "%")):
        raise Denied("ACCESS_DENIED")
    parts = tuple(relative.split("/"))
    if any(p in ("", ".", "..") or p.startswith(".") or
           any(fnmatch.fnmatch(p.casefold(), pat) for pat in FORBIDDEN) for p in parts):
        raise Denied("ACCESS_DENIED")
    return parts


def identity(s: os.stat_result) -> tuple:
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


class SafeRoot:
    def __init__(self, root: Path):
        if root.is_symlink():
            raise Denied("ACCESS_DENIED")
        self.path = root.resolve(strict=True)
        s = self.path.stat()
        if not stat.S_ISDIR(s.st_mode):
            raise Denied("ACCESS_DENIED")
        self.root_identity = (s.st_dev, s.st_ino)

    def open_root(self) -> int:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            raise Denied("SOURCE_OFFLINE") from None
        s = os.fstat(fd)
        if (s.st_dev, s.st_ino) != self.root_identity:
            os.close(fd)
            raise Denied("SOURCE_OFFLINE")
        return fd

    def read(self, relative: str) -> tuple[bytes, os.stat_result]:
        parts = parts_for(relative)
        fd = self.open_root()
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = os.fstat(file_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise Denied("ACCESS_DENIED")
                if before.st_size > MAX_FILE_BYTES:
                    raise Denied("FILE_TOO_LARGE")
                if getattr(before, "st_flags", 0) & getattr(stat, "SF_DATALESS", 0x40000000):
                    raise Denied("NOT_DOWNLOADED")
                chunks, length = [], 0
                while chunk := os.read(file_fd, 65536):
                    length += len(chunk)
                    if length > MAX_FILE_BYTES:
                        raise Denied("FILE_TOO_LARGE")
                    chunks.append(chunk)
                after = os.fstat(file_fd)
                entry = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                if identity(before) != identity(after) or identity(after) != identity(entry):
                    raise Denied("SOURCE_CHANGED")
                return b"".join(chunks), after
            finally:
                os.close(file_fd)
        except FileNotFoundError:
            raise Denied("NOT_FOUND") from None
        except OSError:
            raise Denied("ACCESS_DENIED") from None
        finally:
            os.close(fd)

    def entries(self) -> tuple[list[str], list[dict]]:
        files, gaps = [], []
        seen = 0

        def walk(fd: int, prefix: str, depth: int):
            nonlocal seen
            with os.scandir(fd) as entries:
                for entry in entries:
                    seen += 1
                    if seen > 10000:
                        raise Denied("INDEX_INCOMPLETE")
                    rel = prefix + entry.name
                    try:
                        parts_for(rel)
                        if entry.is_symlink():
                            raise Denied("ACCESS_DENIED")
                        if entry.is_dir(follow_symlinks=False):
                            if depth >= 12:
                                raise Denied("DEPTH_LIMIT")
                            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                            try:
                                walk(child, rel + "/", depth + 1)
                            finally:
                                os.close(child)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(rel)
                        else:
                            raise Denied("UNSUPPORTED_TYPE")
                    except (Denied, OSError) as error:
                        gaps.append({"code": error.code if isinstance(error, Denied) else "ACCESS_DENIED"})
        root_fd = self.open_root()
        try:
            walk(root_fd, "", 0)
        finally:
            os.close(root_fd)
        return sorted(files), gaps
