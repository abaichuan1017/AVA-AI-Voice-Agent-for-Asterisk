import multiprocessing
import sqlite3
import time

import pytest

from src.core.calendly_store import CalendlyStore
from src.tools.business.calendly_client import CalendlyError

IDENTITY = {'uri': 'https://api.calendly.com/users/test', 'current_organization': 'https://api.calendly.com/organizations/test', 'timezone': 'UTC'}
PAYLOAD = {'event_type': 'https://api.calendly.com/event_types/test', 'start_time': '2030-01-01T12:00:00Z', 'invitee': {'email': 'test@example.com'}}


def claim(paths):
    store = CalendlyStore(*paths)
    _, admitted = store.admit('call:concurrent', store.connection(), PAYLOAD, {})
    return admitted


def test_processes_share_one_admission_and_restart_never_resets_claim(tmp_path):
    paths = (str(tmp_path / 'db'), str(tmp_path / 'token'))
    store = CalendlyStore(*paths)
    conn = store.save_connection(IDENTITY, 'synthetic-private-token', 0)
    store.update_connection(conn['revision'], enabled=True)
    with multiprocessing.get_context('fork').Pool(4) as workers:
        assert sum(workers.map(claim, [paths] * 8)) == 1
    row = CalendlyStore(*paths).prior('call:concurrent')
    assert row['state'] == 'submitting'
    with store.db() as db: db.execute('UPDATE attempts SET lease=0,next_due=0')
    store.lease_check(row['id'], False)
    assert store.attempt(row['id'])['state'] == 'uncertain'
    assert claim(paths) is False


def test_newer_schema_and_journal_lock_fail_closed(tmp_path):
    paths = (str(tmp_path / 'db'), str(tmp_path / 'token'))
    store = CalendlyStore(*paths)
    conn = store.save_connection(IDENTITY, 'synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError): claim(paths)
        assert time.monotonic() - start < 2
        db.rollback()
        assert not store.prior('call:concurrent')
        db.execute('PRAGMA user_version=99')
    with pytest.raises(CalendlyError, match='journal_version_unsupported'): CalendlyStore(*paths)


def test_interrupted_token_replace_stays_disabled(tmp_path, monkeypatch):
    store = CalendlyStore(str(tmp_path / 'db'), str(tmp_path / 'token'))
    conn = store.save_connection(IDENTITY, 'old-synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    def fail(*args): raise OSError('injected replace failure')
    monkeypatch.setattr('os.replace', fail)
    with pytest.raises(OSError): store.save_connection(IDENTITY, 'new-synthetic', conn['revision'])
    assert not store.connection()['enabled']
    with pytest.raises(CalendlyError, match='credential_revision_mismatch'): store.credentials()
