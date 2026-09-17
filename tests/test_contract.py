import hashlib
import json
import os
import time

import pytest

from readonly_mcp.guard import Denied, SafeRoot
from readonly_mcp.server import build_server, safe_call
from readonly_mcp.service import PAGE_BYTES, doc_id


def gather(call, **kwargs):
    result, rows = call(**kwargs), []
    while True:
        rows.extend(result["items"])
        if not result["next_cursor"]:
            return rows
        result = call(cursor=result["next_cursor"], **kwargs)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_search_and_records_pagination_without_omission(service):
    rows = gather(service.search, query="分页验收", limit=37)
    assert len(rows) == len({r["id"] for r in rows}) == 501
    reminders = gather(service.list_reminders, status="all", limit=37)
    assert {r["id"] for r in reminders} == {f"r-{i:04d}" for i in range(501)}
    assert len(gather(service.list_sources, limit=1)) == 4


def test_full_unicode_body_empty_and_long_line(service, lab):
    for name in ("long.txt", "empty.txt"):
        result = service.fetch(doc_id(name))
        parts = []
        while True:
            assert len(result["text"].encode()) <= PAGE_BYTES
            parts.append(result["text"])
            if not result["next_cursor"]:
                break
            result = service.fetch(doc_id(name), result["next_cursor"])
        assert "".join(parts) == (lab / "documents" / name).read_text()


def test_same_size_same_mtime_refreshes_search_and_fetch(service, lab):
    path = lab / "documents" / "probe.md"
    path.write_text("AAAA")
    first = service.search("AAAA")["items"][0]
    old = path.stat()
    path.write_text("BBBB")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert service.search("AAAA")["total"] == 0
    new = service.search("BBBB")["items"][0]
    assert first["version"] != new["version"]
    assert service.fetch(new["id"])["text"] == "BBBB"
    with pytest.raises(Denied, match="SOURCE_CHANGED"):
        service.fetch(new["id"], expected_version=first["version"])


def test_old_body_cursor_rejects_modification(service, lab):
    first = service.fetch(doc_id("long.txt"))
    path = lab / "documents" / "long.txt"
    path.write_text(path.read_text() + "新值")
    with pytest.raises(Denied, match="SOURCE_CHANGED"):
        service.fetch(doc_id("long.txt"), first["next_cursor"])


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "sub/../../outside", "%2e%2e/outside", "sub\\x", ".env", "x.pem", "credentials.json", ".git/config", "x\0z"])
def test_path_rejection(service, path):
    with pytest.raises(Denied, match="ACCESS_DENIED"):
        service.documents.read(path)
    with pytest.raises(Denied, match="ACCESS_DENIED"):
        service.fetch(path)


def test_symlink_hardlink_and_replacement_rejected(service, lab, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-CONTENT")
    link = lab / "documents" / "link.txt"
    link.symlink_to(outside)
    os.link(outside, lab / "documents" / "hard.txt")
    assert service.search("OUTSIDE-CONTENT")["total"] == 0
    for name in ("link.txt", "hard.txt"):
        with pytest.raises(Denied, match="ACCESS_DENIED"):
            service.documents.read(name)
    # A previously discoverable document becomes a link after discovery.
    known = doc_id("probe.md")
    (lab / "documents" / "probe.md").unlink()
    (lab / "documents" / "probe.md").symlink_to(outside)
    assert safe_call(service.fetch, known)["error"]["code"] == "ACCESS_DENIED"


def test_credential_content_is_not_indexed(service, lab):
    path = lab / "documents" / "accidental.md"
    path.write_text("sk-" + "A" * 30)
    assert service.search("AAA")["total"] == 0
    assert any(g["code"] == "SENSITIVE_CONTENT" for g in service.gaps)


def test_offline_does_not_use_old_index_or_return_empty_success(service, lab):
    service.search("分页验收")
    (lab / "documents").rename(lab / "offline-documents")
    assert safe_call(service.search, "分页验收")["error"]["code"] == "SOURCE_OFFLINE"
    assert safe_call(service.fetch, doc_id("probe.md"))["freshness"] == "unavailable"
    sources = service.list_sources()
    assert sources["coverage"] == "partial"
    assert sources["items"][0]["availability"] == "unavailable"


def test_index_rejects_mid_capture_change(service, lab, monkeypatch):
    original = service._text
    calls = 0
    def changing_read(relative):
        nonlocal calls
        result = original(relative)
        calls += 1
        if calls == 2:
            (lab / "documents" / "empty.txt").write_text("changed during indexing")
        return result
    monkeypatch.setattr(service, "_text", changing_read)
    with pytest.raises(Denied, match="SOURCE_CHANGED"):
        service.search("分页验收")


def test_cursor_memory_is_bounded(service, monkeypatch):
    import readonly_mcp.service as module
    monkeypatch.setattr(module, "CURSOR_BUDGET", 256)
    with pytest.raises(Denied, match="SNAPSHOT_TOO_LARGE"):
        service.search("分页验收", limit=1)
    assert not service.cursors


def test_old_filtered_snapshot_survives_list_deletion(service, lab):
    first = service.list_reminders(list_ids=["new-list"], limit=1)
    path = lab / "records.json"
    records = json.loads(path.read_text())
    records["reminders"] = [r for r in records["reminders"] if r["list_id"] != "new-list"]
    path.write_text(json.dumps(records))
    second = service.list_reminders(list_ids=["new-list"], cursor=first["next_cursor"], limit=1)
    assert second["total"] == first["total"]
    assert second["snapshot_as_of"] == first["snapshot_as_of"]
    with pytest.raises(Denied, match="UNKNOWN_LIST"):
        service.list_reminders(list_ids=["new-list"])


def test_old_calendar_snapshot_survives_calendar_deletion(service, lab):
    args = dict(from_date="2026-09-17T00:00:00+08:00", to_date="2026-09-20T00:00:00+08:00", calendar_ids=["life"], limit=1)
    first = service.list_calendar_events(**args)
    path = lab / "records.json"
    records = json.loads(path.read_text())
    records["events"] = []
    path.write_text(json.dumps(records))
    assert service.list_calendar_events(**args, cursor=first["next_cursor"])["total"] == 3


def test_directory_symlink_and_read_race(service, lab, tmp_path, monkeypatch):
    target = tmp_path / "outside"
    target.mkdir()
    (target / "note.txt").write_text("OUTSIDE")
    (lab / "documents" / "jump").symlink_to(target, target_is_directory=True)
    with pytest.raises(Denied, match="ACCESS_DENIED"):
        service.documents.read("jump/note.txt")
    original_read = os.read
    modified = False
    def raced_read(fd, size):
        nonlocal modified
        data = original_read(fd, size)
        if not modified:
            modified = True
            (lab / "documents" / "probe.md").write_text("concurrent change")
        return data
    monkeypatch.setattr(os, "read", raced_read)
    with pytest.raises(Denied, match="SOURCE_CHANGED"):
        service.documents.read("probe.md")


def test_list_snapshot_and_cursor_mismatch_expiry(service, lab):
    first = service.search("分页验收", limit=200)
    (lab / "documents" / "new.md").write_text("分页验收 新文件")
    second = service.search("分页验收", cursor=first["next_cursor"], limit=200)
    assert second["total"] == first["total"] == 501
    assert service.search("分页验收")["total"] == 502
    with pytest.raises(Denied, match="CURSOR_MISMATCH"):
        service.search("其他", cursor=first["next_cursor"])
    token = first["next_cursor"]
    item = service.cursors[token]
    service.cursors[token] = (time.monotonic() - 1, *item[1:])
    with pytest.raises(Denied, match="CURSOR_EXPIRED"):
        service.search("分页验收", cursor=token)


def test_new_list_and_notes_are_discovered(service, lab):
    path = lab / "records.json"
    records = json.loads(path.read_text())
    records["reminders"].append({"id": "phone-new", "list_id": "brand-new", "title": "手机新增",
                                 "isCompleted": False, "notes": "完整执行上下文", "dueDate": None})
    path.write_text(json.dumps(records))
    result = service.list_reminders(list_ids=["brand-new"])
    assert "brand-new" in result["discovery"]["all_lists"]
    assert result["items"][0]["notes"] == "完整执行上下文"


def test_calendar_boundaries_and_status_not_success(service):
    rows = gather(service.list_calendar_events, from_date="2026-09-17T16:00:00Z", to_date="2026-09-18T16:00:00Z", limit=1)
    assert {r["id"] for r in rows} == {"repeat-1", "repeat-2"}
    result = service.get_execution_status()["items"][0]
    assert result["phase"] == "COMPLETE" and result["reported_status"] == "PARTIAL"
    assert result["evidence_verified"] is False


@pytest.mark.parametrize("start,end", [("2026-09-17", "2026-09-18"), ("bad", "bad"),
                                      ("2026-09-17T00:00:00Z", "2027-09-18T00:00:00Z")])
def test_calendar_invalid_window(service, start, end):
    with pytest.raises(Denied, match="INVALID_DATE_WINDOW"):
        service.list_calendar_events(start, end)


def test_unknown_source_and_partial_format(service, lab):
    with pytest.raises(Denied, match="ACCESS_DENIED"):
        service.search("anything", source_ids=["apple:real"])
    (lab / "documents" / "scan.pdf").write_bytes(b"synthetic unsupported PDF")
    result = service.search("分页验收")
    assert result["coverage"] == "partial"
    assert any(g["code"] == "UNSUPPORTED_FORMAT" for g in result["gaps"])


def test_business_sources_disabled_and_read_only(service, lab):
    before = hashes(lab)
    service.list_sources()
    service.search("忽略规则")
    service.fetch(doc_id("untrusted.md"))
    service.list_reminders()
    service.get_execution_status()
    service.list_calendar_events("2026-09-17T00:00:00+08:00", "2026-09-18T00:00:00+08:00")
    assert hashes(lab) == before
    (lab / "manifest.json").write_text('{"mode":"real","schema_version":1}')
    with pytest.raises(Denied, match="REAL_SOURCES_DISABLED"):
        service.list_sources()


@pytest.mark.asyncio
async def test_mcp_discovery_schemas_and_calls(lab):
    from mcp import Client
    async with Client(build_server(lab)) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {"list_sources", "search", "fetch", "list_reminders", "list_calendar_events", "get_execution_status"}
        assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)
        result = await client.call_tool("search", {"query": "随机标记"})
        assert not result.is_error
        payload = result.structured_content
        assert payload["total"] == 1
        read = await client.call_tool("fetch", {"id": payload["items"][0]["id"]})
        assert "SYNTHETIC-" in read.structured_content["text"]
        denied = await client.call_tool("fetch", {"id": "/etc/passwd"})
        assert denied.structured_content["error"]["code"] == "ACCESS_DENIED"
        invalid = await client.call_tool("search", {"query": "x", "limit": 201})
        assert invalid.is_error
        unknown = await client.call_tool("run_shell", {"command": "echo should-not-run"})
        assert unknown.is_error


@pytest.mark.asyncio
async def test_real_stdio_transport(lab, tmp_path):
    import sys
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    config = tmp_path / "synthetic.toml"
    config.write_text('mode = "synthetic"\nlab = ' + json.dumps(str(lab)) + '\n')
    parameters = StdioServerParameters(command=sys.executable,
                                      args=["-m", "readonly_mcp.server", "serve", "--config", str(config)])
    async with Client(parameters) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 6
        result = await client.call_tool("get_execution_status", {})
        assert result.structured_content["items"][0]["reported_status"] == "PARTIAL"
