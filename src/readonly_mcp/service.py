"""Deterministic query contract backed exclusively by an operator-created lab."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from .guard import Denied, SafeRoot

SOURCE_IDS = ("synthetic:documents", "synthetic:reminders", "synthetic:calendar", "synthetic:execution")
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".json", ".ndjson"}
PAGE_BYTES = 32768
INDEX_BUDGET = 256 * 1024 * 1024
CURSOR_TTL = 300
CURSOR_BUDGET = 8 * 1024 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def doc_id(relative: str) -> str:
    return "doc_" + hashlib.sha256(relative.encode()).hexdigest()


def validate_limit(limit: int):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise Denied("INVALID_ARGUMENT")


def envelope(source: str, version: str | None, *, observed: str | None = None,
             gaps: list | None = None, modified: str | None = None) -> dict:
    gaps = gaps or []
    return {"source_id": source, "authority": "synthetic-fixture", "source_ref": source,
            "observed_at": observed or now(), "source_modified_at": modified, "version": version,
            "freshness": "synthetic", "cache_as_of": None, "upstream_sync_status": "not_applicable",
            "coverage": "partial" if gaps else "complete", "gaps": gaps,
            "truncated": False, "next_cursor": None}


class QueryService:
    def __init__(self, lab: Path):
        self.root = SafeRoot(lab)
        self.check_lab()
        self.documents = SafeRoot(lab / "documents")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute("CREATE VIRTUAL TABLE docs USING fts5(id UNINDEXED, path UNINDEXED, body, tokenize='trigram')")
        self.catalog: dict[str, dict] = {}
        self.gaps: list[dict] = []
        self.version: str | None = None
        self.observed_at: str | None = None
        self.last_refresh_error: str | None = None
        self.cursors: OrderedDict[str, tuple] = OrderedDict()

    def check_lab(self):
        raw, _ = self.root.read("manifest.json")
        try:
            manifest = json.loads(raw)
        except (ValueError, UnicodeError):
            raise Denied("INVALID_LAB") from None
        if manifest != {"mode": "synthetic", "schema_version": 1}:
            raise Denied("REAL_SOURCES_DISABLED")

    def close(self):
        self.db.close()

    def _text(self, relative: str):
        raw, s = self.documents.read(relative)
        if Path(relative).suffix.casefold() not in TEXT_SUFFIXES:
            raise Denied("UNSUPPORTED_FORMAT")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise Denied("UNSUPPORTED_ENCODING") from None
        if "\0" in text:
            raise Denied("UNSUPPORTED_FORMAT")
        # Defense in depth for recognizable accidentally embedded credentials.
        if re.search(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|\b(?:sk-|ghp_)[A-Za-z0-9_-]{20,}", text):
            raise Denied("SENSITIVE_CONTENT")
        return text, digest(raw), datetime.fromtimestamp(s.st_mtime, timezone.utc).isoformat()

    def refresh(self):
        """Hash authorized files on every search; metadata alone is not a version."""
        with self.lock:
            self.check_lab()
            started = time.monotonic()
            paths, gaps = self.documents.entries()
            rows, catalog, budget = [], {}, 0
            for relative in paths:
                if time.monotonic() - started > 20:
                    raise Denied("INDEX_INCOMPLETE")
                identifier = doc_id(relative)
                try:
                    text, version, modified = self._text(relative)
                    budget += len(text.encode())
                    if budget > INDEX_BUDGET:
                        raise Denied("INDEX_INCOMPLETE")
                except Denied as error:
                    if error.code == "INDEX_INCOMPLETE":
                        raise
                    gaps.append({"id": identifier, "code": error.code})
                    continue
                catalog[identifier] = {"path": relative, "version": version, "modified": modified}
                rows.append((identifier, relative, text))
            end_paths, end_gaps = self.documents.entries()
            if paths != end_paths or gaps[:len(end_gaps)] != end_gaps:
                raise Denied("SOURCE_CHANGED")
            for entry in catalog.values():
                if time.monotonic() - started > 20:
                    raise Denied("INDEX_INCOMPLETE")
                _, current_version, _ = self._text(entry["path"])
                if current_version != entry["version"]:
                    raise Denied("SOURCE_CHANGED")
            with self.db:
                self.db.execute("DELETE FROM docs")
                self.db.executemany("INSERT INTO docs VALUES (?, ?, ?)", rows)
            self.catalog, self.gaps = catalog, gaps
            self.version = digest(json.dumps(catalog, sort_keys=True).encode())
            self.observed_at = now()
            self.last_refresh_error = None

    def _remember(self, token: str, record: tuple):
        self.cursors[token] = record
        for key, value in list(self.cursors.items()):
            if value[0] < time.monotonic():
                del self.cursors[key]
        def retained_bytes():
            unique = {id(r[3]): (r[3], r[4]) for r in self.cursors.values() if r[1] != "fetch"}
            return sum(len(json.dumps(v, ensure_ascii=False).encode()) for v in unique.values())
        while len(self.cursors) > 256 or retained_bytes() > CURSOR_BUDGET:
            self.cursors.popitem(last=False)
        if token not in self.cursors:
            raise Denied("SNAPSHOT_TOO_LARGE")

    def _page(self, kind: str, args: dict, items: list | None, meta: dict | None,
              cursor: str | None, limit: int) -> dict:
        validate_limit(limit)
        fingerprint = json.dumps(args, sort_keys=True)
        offset = 0
        expiry = time.monotonic() + CURSOR_TTL
        if cursor:
            record = self.cursors.get(cursor)
            if not record or record[0] < time.monotonic():
                raise Denied("CURSOR_EXPIRED")
            expiry, old_kind, old_args, items, meta, offset = record
            if old_kind != kind or old_args != fingerprint:
                raise Denied("CURSOR_MISMATCH")
        assert items is not None and meta is not None
        result = {**meta, "items": items[offset:offset + limit], "offset": offset, "total": len(items)}
        result["snapshot_as_of"] = meta["observed_at"]
        nxt = offset + len(result["items"])
        if nxt < len(items):
            token = secrets.token_urlsafe(24)
            self._remember(token, (expiry, kind, fingerprint, items, meta, nxt))
            result.update(next_cursor=token, truncated=True)
        return result

    def list_sources(self, cursor: str | None = None, limit: int = 50) -> dict:
        with self.lock:
            self.check_lab()
            items = [{"id": s, "authority": "synthetic-fixture", "mode": "synthetic",
                      "capabilities": ["search", "fetch"] if s.endswith("documents") else ["list"],
                      "real_adapter_enabled": False} for s in SOURCE_IDS]
            for item in items:
                try:
                    if item["id"] == SOURCE_IDS[0]:
                        fd = self.documents.open_root()
                        os.close(fd)
                        item.update(index_observed_at=self.observed_at, index_error=self.last_refresh_error,
                                    indexed_documents=len(self.catalog), index_gaps=self.gaps,
                                    index_state="ready" if self.version else "not_built")
                    else:
                        self._records()
                    item["availability"] = "readable_locally"
                except Denied as error:
                    item.update(availability="unavailable", error=error.code)
            gaps = [{"source_id": i["id"], "code": i["error"]} for i in items if "error" in i]
            return self._page("sources", {}, items,
                              envelope("synthetic:registry", digest(json.dumps(items).encode()), gaps=gaps), cursor, limit)

    def search(self, query: str, source_ids: list[str] | None = None,
               cursor: str | None = None, limit: int = 50) -> dict:
        with self.lock:
            self.check_lab()
            if not isinstance(query, str) or not query.strip() or len(query) > 512:
                raise Denied("INVALID_ARGUMENT")
            if source_ids is not None and source_ids != [SOURCE_IDS[0]]:
                raise Denied("ACCESS_DENIED")
            args = {"query": query, "source_ids": source_ids}
            if cursor:
                fd = self.documents.open_root()
                os.close(fd)
                return self._page("search", args, None, None, cursor, limit)
            self.refresh()
            if len(query) >= 3:
                sql = "SELECT id,path,body FROM docs WHERE docs MATCH ? ORDER BY path"
                params = ('body : "' + query.replace('"', '""') + '"',)
            else:
                sql = "SELECT id,path,body FROM docs WHERE instr(lower(body),lower(?))>0 ORDER BY path"
                params = (query,)
            hits = []
            for identifier, path, body in self.db.execute(sql, params):
                start = max(0, body.casefold().find(query.casefold()) - 80)
                entry = self.catalog[identifier]
                hits.append({"id": identifier, "title": path, "text": body[start:start + 512],
                             "source_ref": f"synthetic://documents/{identifier}", "version": entry["version"]})
            meta = envelope(SOURCE_IDS[0], self.version, observed=self.observed_at, gaps=self.gaps)
            meta["snapshot_semantics"] = "immutable_search_snapshot; fetch revalidates current bytes"
            return self._page("search", args, hits, meta, None, limit)

    def fetch(self, identifier: str, cursor: str | None = None, expected_version: str | None = None) -> dict:
        with self.lock:
            self.check_lab()
            if not re.fullmatch(r"doc_[0-9a-f]{64}", identifier):
                raise Denied("ACCESS_DENIED")
            if identifier not in self.catalog:
                self.refresh()
            if identifier not in self.catalog:
                raise Denied("NOT_FOUND")
            entry = self.catalog[identifier]
            text, version, modified = self._text(entry["path"])
            offset, expiry = 0, time.monotonic() + CURSOR_TTL
            if cursor:
                saved = self.cursors.get(cursor)
                if not saved or saved[0] < time.monotonic():
                    raise Denied("CURSOR_EXPIRED")
                expiry, kind, old_id, old_version, _, offset = saved
                if kind != "fetch" or old_id != identifier:
                    raise Denied("CURSOR_MISMATCH")
                if version != old_version:
                    raise Denied("SOURCE_CHANGED")
            if expected_version is not None and version != expected_version:
                raise Denied("SOURCE_CHANGED")
            chunk = text[offset:offset + PAGE_BYTES].encode()[:PAGE_BYTES].decode("utf-8", errors="ignore")
            nxt = offset + len(chunk)
            result = envelope(SOURCE_IDS[0], version, modified=modified)
            result.update(id=identifier, title=entry["path"], source_ref=f"synthetic://documents/{identifier}",
                          text=chunk, offset=offset, next_offset=nxt, total_characters=len(text),
                          position_unit="unicode_code_points")
            if nxt < len(text):
                token = secrets.token_urlsafe(24)
                self._remember(token, (expiry, "fetch", identifier, version, None, nxt))
                result.update(next_cursor=token, truncated=True)
            return result

    def _records(self) -> tuple[dict, str, str]:
        self.check_lab()
        raw, stat = self.root.read("records.json")
        if len(raw) > 2 * 1024 * 1024:
            raise Denied("RECORDS_TOO_LARGE")
        try:
            data = json.loads(raw)
            if set(data) != {"reminders", "events", "runs"} or not all(isinstance(x, list) for x in data.values()):
                raise ValueError
            for rows in data.values():
                ids = [x["id"] for x in rows]
                if len(ids) != len(set(ids)):
                    raise ValueError
        except (ValueError, KeyError, TypeError):
            raise Denied("INVALID_SYNTHETIC_RECORDS") from None
        return data, digest(raw), datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()

    def list_reminders(self, status: str = "open", list_ids: list[str] | None = None,
                       cursor: str | None = None, limit: int = 50) -> dict:
        with self.lock:
            data, version, modified = self._records()
            if status not in ("open", "completed", "all"):
                raise Denied("INVALID_ARGUMENT")
            if cursor:
                return self._page("reminders", {"status": status, "list_ids": list_ids}, None, None, cursor, limit)
            rows = data["reminders"]
            lists = sorted({r["list_id"] for r in rows})
            if list_ids is not None and not set(list_ids) <= set(lists):
                raise Denied("UNKNOWN_LIST")
            filtered = [r for r in rows if (status == "all" or r["isCompleted"] == (status == "completed"))
                        and (list_ids is None or r["list_id"] in list_ids)]
            meta = envelope(SOURCE_IDS[1], version, modified=modified)
            meta.update(discovery={"all_lists": lists, "total_records": len(rows), "synthetic_only": True})
            return self._page("reminders", {"status": status, "list_ids": list_ids},
                              sorted(filtered, key=lambda r: r["id"]), meta, cursor, limit)

    def list_calendar_events(self, from_date: str, to_date: str, calendar_ids: list[str] | None = None,
                             cursor: str | None = None, limit: int = 50) -> dict:
        with self.lock:
            try:
                start, end = datetime.fromisoformat(from_date), datetime.fromisoformat(to_date)
                if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end - start <= timedelta(days=90):
                    raise ValueError
            except ValueError:
                raise Denied("INVALID_DATE_WINDOW") from None
            data, version, modified = self._records()
            if cursor:
                return self._page("calendar", {"from": from_date, "to": to_date, "calendar_ids": calendar_ids},
                                  None, None, cursor, limit)
            rows = data["events"]
            calendars = sorted({r["calendar_id"] for r in rows})
            if calendar_ids is not None and not set(calendar_ids) <= set(calendars):
                raise Denied("UNKNOWN_CALENDAR")
            filtered = [r for r in rows if datetime.fromisoformat(r["start"]) < end and
                        datetime.fromisoformat(r["end"]) > start and
                        (calendar_ids is None or r["calendar_id"] in calendar_ids)]
            meta = envelope(SOURCE_IDS[2], version, modified=modified)
            meta.update(window={"from": from_date, "to": to_date, "end_exclusive": True}, calendars=calendars)
            return self._page("calendar", {"from": from_date, "to": to_date, "calendar_ids": calendar_ids},
                              sorted(filtered, key=lambda r: (r["start"], r["id"])), meta, cursor, limit)

    def get_execution_status(self, source_id: str = "synthetic:execution", run_id: str | None = None,
                             cursor: str | None = None, limit: int = 50) -> dict:
        with self.lock:
            if source_id != SOURCE_IDS[3]:
                raise Denied("ACCESS_DENIED")
            data, version, modified = self._records()
            allowed = ("id", "phase", "reported_status", "started_at", "ended_at", "blockers")
            rows = [{**{k: r.get(k) for k in allowed}, "evidence_verified": False}
                    for r in data["runs"] if run_id is None or r["id"] == run_id]
            meta = envelope(SOURCE_IDS[3], version, modified=modified,
                            gaps=[{"code": "SYNTHETIC_EVIDENCE_NOT_BUSINESS_PROOF"}])
            return self._page("execution", {"source_id": source_id, "run_id": run_id},
                              sorted(rows, key=lambda r: r["id"]), meta, cursor, limit)
