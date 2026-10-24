"""Cross-store deletion survives a journal fault and process restart."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.core.call_history import CallHistoryStore, CallRecord
from src.core.calendly_store import CalendlyStore
from src.tools.business.calendly_client import CalendlyError


@pytest.mark.parametrize('mode', ['single', 'retention'])
@pytest.mark.parametrize('fault', [sqlite3.OperationalError, CalendlyError, OSError, None])
@pytest.mark.parametrize('history_enabled_after_restart', ['true', 'false'])
async def test_history_deletion_retries_private_erasure_after_restart(tmp_path, monkeypatch, mode, fault, history_enabled_after_restart):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(tmp_path / 'calendly.db'))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(tmp_path / 'token.json'))
    journal = CalendlyStore()
    with journal.db() as db:
        db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            'attempt', 'call:purged', 'conn', 'digest', 'duplicate', 'marker', 'confirmed',
            '{"email":"test@example.com"}', '{"appointment_name":"Test"}',
            datetime.now(timezone.utc).timestamp(), 0, 0, 0, 0, 0))
    path = str(tmp_path / 'history.db')
    history = CallHistoryStore(path)
    now = datetime.now(timezone.utc)
    await history.save(CallRecord(call_id='purged', start_time=now-timedelta(days=2), end_time=now))
    row = await history.get_by_call_id('purged')
    from src.core import calendly_store
    real = calendly_store.scrub_deleted_calls
    def fail(_): raise fault('injected')
    if fault is None:
        journal.path.rename(tmp_path / 'temporarily-unavailable.db')
    else:
        monkeypatch.setattr(calendly_store, 'scrub_deleted_calls', fail)
    if mode == 'single': assert await history.delete(row.id)
    else: assert await history.delete_before(now) == 1
    assert await history.get_by_call_id('purged') is None
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('purged',)]
    monkeypatch.setattr(calendly_store, 'scrub_deleted_calls', real)
    if fault is None:
        assert not journal.path.exists()  # No empty replacement journal was created.
        (tmp_path / 'temporarily-unavailable.db').rename(journal.path)
    monkeypatch.setenv('CALL_HISTORY_ENABLED', history_enabled_after_restart)
    await CallHistoryStore(path).retry_pending_calendly_purges()
    erased = journal.attempt('attempt')
    assert erased['private'] == erased['public'] == '{}'
    assert erased['operation'] == 'call:purged' and erased['duplicate_key'] == 'duplicate'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []


@pytest.mark.parametrize('legacy_file', [False, True])
async def test_disabled_history_retry_does_not_initialize_a_new_queue(tmp_path, monkeypatch, legacy_file):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'false')
    path = tmp_path / 'disabled.db'
    if legacy_file:
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE legacy (id TEXT)')
    store = CallHistoryStore(str(path))
    await store.retry_pending_calendly_purges()
    if not legacy_file:
        assert not path.exists()
    else:
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('legacy',)]
