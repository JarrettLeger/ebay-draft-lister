import json
import sqlite3
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from test_app import image


@pytest.fixture
def batch_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('CLOUDINARY_CLOUD_NAME', 'test')
    monkeypatch.setenv('CLOUDINARY_UPLOAD_PRESET', 'test')
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    return TestClient(create_app(tmp_path)), tmp_path


def imports(client, count=3):
    return client.post('/api/import', files=[('files', (f'{i}.jpg', image())) for i in range(count)]).json()


def finish(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get('/api/jobs').json()[0]
        if job['state'] not in ('running', 'cancelling'):
            return job
        time.sleep(0.01)
    pytest.fail('Batch did not finish')


def test_batch_continues_after_failure_and_persists(batch_workspace, monkeypatch):
    client, root = batch_workspace
    cards = imports(client)
    calls = []
    def upload(path):
        calls.append(path.name)
        if path.name == cards[0]['front']:
            raise RuntimeError('offline')
        return 'https://example.com/photo.jpg'
    monkeypatch.setattr('backend.services.upload', upload)
    response = client.post('/api/jobs', json={'operation': 'upload', 'ids': [c['id'] for c in cards]})
    assert response.status_code == 200
    job = finish(client)
    assert job['state'] == 'completed' and job['completed'] == 3 and job['failed'] == 1
    assert len(calls) == 3
    assert len(job['results']) == 3
    persisted = TestClient(create_app(root)).get('/api/jobs').json()[0]
    assert persisted == job
    assert all(not c.get('job_id') for c in client.get('/api/cards').json())


def test_cancel_skips_remaining_and_reserves_cards(batch_workspace, monkeypatch):
    client, _ = batch_workspace
    cards = imports(client)
    started, release = Event(), Event()
    calls = []
    def upload(path):
        calls.append(path.name)
        started.set()
        assert release.wait(5)
        return 'https://example.com/photo.jpg'
    monkeypatch.setattr('backend.services.upload', upload)
    response = client.post('/api/jobs', json={'operation': 'upload', 'ids': [c['id'] for c in cards]})
    ident = response.json()['id']
    try:
        assert started.wait(5)
        progress = client.get('/api/jobs').json()[0]
        assert progress['current'] == cards[0]['id'] and progress['completed'] == 0
        queued = cards[1]
        assert client.put(f"/api/cards/{queued['id']}", json=queued['listing']).status_code == 409
        assert client.post(f"/api/cards/{queued['id']}/upload").status_code == 409
        assert client.post('/api/jobs', json={'operation': 'upload', 'ids': [queued['id']]}).status_code == 409
        assert client.post(f"/api/cards/{queued['id']}/pair", json={'back_id': cards[2]['id']}).status_code == 409
        assert client.post('/api/export', json={'ids': [queued['id']]}).status_code == 400
        assert client.post(f'/api/jobs/{ident}/cancel').json()['state'] == 'cancelling'
    finally:
        release.set()
    job = finish(client)
    assert job['state'] == 'cancelled' and job['completed'] == 1
    assert calls == [cards[0]['front']]
    saved = {c['id']: c for c in client.get('/api/cards').json()}
    assert saved[cards[1]['id']]['state'] == 'imported'
    assert saved[cards[0]['id']]['front_url']
    assert client.post(f'/api/jobs/{ident}/cancel').json()['state'] == 'cancelled'


def test_batch_validation_is_atomic(batch_workspace, monkeypatch):
    client, _ = batch_workspace
    card = imports(client, 1)[0]
    assert client.post('/api/jobs', json={'operation': 'upload', 'ids': []}).status_code == 422
    assert client.post('/api/jobs', json={'operation': 'delete', 'ids': [card['id']]}).status_code == 422
    assert client.post('/api/jobs', json={'operation': 'upload', 'ids': [card['id'], 'missing']}).status_code == 404
    monkeypatch.delenv('CLOUDINARY_UPLOAD_PRESET')
    assert client.post('/api/jobs', json={'operation': 'upload', 'ids': [card['id']]}).status_code == 400
    assert client.get('/api/jobs').json() == []
    assert client.get('/api/cards').json()[0]['state'] == 'imported'


def test_batch_deduplicates_ids(batch_workspace, monkeypatch):
    client, _ = batch_workspace
    card = imports(client, 1)[0]
    calls = []
    monkeypatch.setattr('backend.services.upload', lambda path: calls.append(path) or 'https://example.com/photo.jpg')
    client.post('/api/jobs', json={'operation': 'upload', 'ids': [card['id'], card['id']]})
    assert finish(client)['completed'] == 1
    assert len(calls) == 1


def test_restart_releases_unstarted_and_marks_interrupted(batch_workspace):
    client, root = batch_workspace
    working, queued = imports(client, 2)
    working.update(state='processing', job_id='batch')
    queued.update(state='queued', before_queue='imported', job_id='batch')
    job = dict(id='batch', state='running', ids=[working['id'], queued['id']], current=working['id'], completed=0)
    with sqlite3.connect(root / 'inventory.sqlite3') as conn:
        for card in (working, queued):
            conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), card['id']))
        conn.execute('INSERT INTO jobs VALUES (?,?)', ('batch', json.dumps(job)))
    restored = TestClient(create_app(root))
    saved = {c['id']: c for c in restored.get('/api/cards').json()}
    assert saved[working['id']]['state'] == 'failed'
    assert saved[queued['id']]['state'] == 'imported'
    assert all('job_id' not in c for c in saved.values())
    assert restored.get('/api/jobs').json()[0]['state'] == 'interrupted'


def test_shutdown_stops_after_current_card(tmp_path, monkeypatch):
    monkeypatch.setenv('CLOUDINARY_CLOUD_NAME', 'test')
    monkeypatch.setenv('CLOUDINARY_UPLOAD_PRESET', 'test')
    started, release = Event(), Event()
    def upload(path):
        started.set()
        assert release.wait(5)
        return 'https://example.com/photo.jpg'
    monkeypatch.setattr('backend.services.upload', upload)
    with TestClient(create_app(tmp_path)) as client:
        cards = imports(client, 2)
        client.post('/api/jobs', json={'operation': 'upload', 'ids': [c['id'] for c in cards]})
        assert started.wait(5)
    release.set()
    job = finish(client)
    assert job['state'] == 'interrupted' and job['completed'] == 1
