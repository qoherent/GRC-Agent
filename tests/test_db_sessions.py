"""Unit tests for db_sessions — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import pytest


def test_recent_sessions_persistence(tmp_path, monkeypatch):
    # Mock settings.env_path to point to a tmp directory
    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))

    from grc_agent.db import delete_session, get_recent_sessions, load_session, save_session

    # Assert initially empty
    assert get_recent_sessions() == []

    # Create two temporary files
    file1 = tmp_path / "graph1.grc"
    file1.touch()
    file2 = tmp_path / "graph2.grc"
    file2.touch()

    # Save session for file1
    sid1 = save_session(None, str(file1), [])
    sessions = get_recent_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid1
    assert sessions[0]["grc_file_path"] == str(file1.resolve())

    # Save session for file2
    sid2 = save_session(None, str(file2), [])
    sessions = get_recent_sessions()
    assert len(sessions) == 2
    # Newest (updated_at desc) should be at index 0
    assert sessions[0]["id"] == sid2
    assert sessions[0]["grc_file_path"] == str(file2.resolve())
    assert sessions[1]["id"] == sid1
    assert sessions[1]["grc_file_path"] == str(file1.resolve())

    # Load session
    s_loaded = load_session(sid1)
    assert s_loaded is not None
    assert s_loaded["grc_file_path"] == str(file1.resolve())

    # Delete file1 and verify it is filtered out from get_recent_sessions (but still loaded by id)
    file1.unlink()
    sessions = get_recent_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid2

    # Delete session
    delete_session(sid2)
    assert len(get_recent_sessions()) == 0


def test_save_session_does_not_resurrect_deleted_session(tmp_path, monkeypatch):
    """Regression: save_session used to silently fall through to an INSERT
    (under a brand-new row id) whenever the given session_id no longer
    existed — e.g. a per-row delete (_on_delete_recent_session) or Clear
    History racing an in-flight save dispatched before the deletion. Must
    now skip the write and return None instead of resurrecting it."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import delete_session, get_recent_sessions, save_session

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [])
    delete_session(sid)

    result = save_session(sid, str(f), [])

    assert result is None, "must signal 'skipped', not fabricate a new id"
    assert get_recent_sessions() == [], "the deleted session must not reappear under a new row"


def test_get_recent_sessions_drops_blob_and_bounds(tmp_path, monkeypatch):
    """DB-3 / UI-4 regression: the recent-sessions list omits the heavy
    messages blob and is bounded by a SQL LIMIT rather than trimming the whole
    table in Python."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import get_recent_sessions, save_session

    for i in range(5):
        f = tmp_path / f"g{i}.grc"
        f.touch()
        save_session(None, str(f), [])

    rows = get_recent_sessions(limit=3)
    assert len(rows) == 3
    assert all("messages" not in r for r in rows)


def test_get_recent_sessions_scans_past_deleted_files(tmp_path, monkeypatch):
    """get_recent_sessions must not truncate valid sessions when earlier
    sessions point to files that no longer exist."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import get_recent_sessions, save_session

    files = []
    for i in range(15):
        f = tmp_path / f"flowgraph_{i}.grc"
        f.touch()
        files.append(f)
        save_session(None, str(f), [])

    # Delete 10 of the files (e.g. newer ones)
    for f in files[5:]:
        f.unlink()

    # get_recent_sessions without limit must return all 5 surviving files
    surviving = get_recent_sessions()
    assert len(surviving) == 5

    # get_recent_sessions(limit=3) must return 3 surviving files
    bounded = get_recent_sessions(limit=3)
    assert len(bounded) == 3


def test_prune_sessions_bounds_growth(tmp_path, monkeypatch):
    """DB-3 regression: an eviction policy caps the sessions table so it does
    not grow without limit (the old JSON store bounded itself to 10 on write)."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import _conn, _prune_in, get_recent_sessions, save_session

    for i in range(8):
        f = tmp_path / f"g{i}.grc"
        f.touch()
        save_session(None, str(f), [])

    with _conn() as conn:
        _prune_in(conn, keep=3)
    assert len(get_recent_sessions(limit=100)) <= 3


def test_db_connection_is_closed_after_use(tmp_path, monkeypatch):
    """DB-4 regression: connections must be explicitly closed — sqlite3's
    `with conn:` only commits/rolls back, it does not close."""
    import sqlite3

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import _conn

    with _conn() as conn:
        conn.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_user_request_and_prompt_flattening():
    """`user_request` builds the one canonical user ModelRequest for both
    text and multimodal prompts; `user_prompt_text`/`prompt_images` flatten
    it back into text and image parts."""
    from pydantic_ai.messages import BinaryContent

    from grc_agent.db import prompt_images, user_prompt_text, user_request

    req = user_request("plain")
    assert user_prompt_text(req.parts[0]) == "plain"
    assert prompt_images(req.parts[0]) == []

    img = BinaryContent(data=b"\x89PNG\r\n\x1a\nDATA", media_type="image/png")
    multi = user_request(["describe", img])
    assert user_prompt_text(multi.parts[0]) == "describe"
    assert prompt_images(multi.parts[0]) == [img]


def test_multimodal_session_roundtrip(tmp_path, monkeypatch):
    """A session saved with an image-bearing user turn reloads with the image
    bytes and media type intact (base64 round-trip through the sanctioned
    ModelMessagesTypeAdapter)."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from pydantic_ai.messages import BinaryContent

    from grc_agent.db import (
        deserialize_messages,
        load_session,
        prompt_images,
        save_session,
        user_request,
    )

    grc = tmp_path / "m.grc"
    grc.write_text("<grc/>")
    img = BinaryContent(data=b"\x89PNG-roundtrip", media_type="image/png")
    sid = save_session(None, str(grc), [user_request(["look at this", img])])
    row = load_session(sid)
    msgs = deserialize_messages(row["messages"])
    imgs = prompt_images(msgs[0].parts[0])
    assert len(imgs) == 1
    assert imgs[0].media_type == "image/png"
    assert imgs[0].data.startswith(b"\x89PNG")
