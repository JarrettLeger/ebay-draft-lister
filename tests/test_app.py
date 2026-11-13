import ast
import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.compat import HEADER_ROW, INFO_ROWS, build_title


@pytest.fixture
def workspace(tmp_path):
    return TestClient(create_app(tmp_path)), tmp_path


def image():
    stream = io.BytesIO()
    Image.new('RGB', (64, 100), 'red').save(stream, 'JPEG')
    return stream.getvalue()


def imported(client, count=1):
    response = client.post('/api/import', files=[('files', (f'photo{i}.jpg', image(), 'image/jpeg')) for i in range(count)])
    assert response.status_code == 200, response.text
    return response.json()


def test_persistence_and_pairing(workspace):
    client, root = workspace
    front, back = imported(client, 2)
    assert client.post(f"/api/cards/{front['id']}/pair", json={'back_id': back['id']}).status_code == 200
    cards = TestClient(create_app(root)).get('/api/cards').json()
    assert len(cards) == 1 and cards[0]['back'] == back['front']
    assert len(list((root / 'photos').iterdir())) == 2
    assert client.post(f"/api/cards/{front['id']}/unpair").status_code == 200
    assert len(client.get('/api/cards').json()) == 2


def test_import_atomic_and_path_safe(workspace):
    client, root = workspace
    response = client.post('/api/import', files=[('files', ('../../original.jpg', image())), ('files', ('bad.jpg', b'not an image'))])
    assert response.status_code == 400
    assert client.get('/api/cards').json() == []
    assert list((root / 'photos').iterdir()) == []
    assert client.get('/api/config', headers={'Host': 'evil.example'}).status_code == 403
    assert client.post('/api/import', headers={'Origin': 'https://evil.example'}).status_code == 403


def test_export_gates_format_and_manual_price(workspace, monkeypatch):
    client, root = workspace
    card = imported(client)[0]
    ident = card['id']
    assert client.post('/api/export', json={'ids': [ident]}).status_code == 400
    listing = card['listing'] | dict(player='Test Player', set_name='Test Set', title='Test, Player', description='Review photos', price='12.34', reviewed=True)
    assert client.put(f'/api/cards/{ident}', json=listing | {'price': '-1'}).status_code == 422
    client.put(f'/api/cards/{ident}', json=listing)
    assert client.post('/api/export', json={'ids': [ident]}).status_code == 400
    monkeypatch.setattr('backend.services.upload', lambda path: 'https://res.cloudinary.com/test/card.jpg')
    assert client.post(f'/api/cards/{ident}/upload').json()['state'] == 'ready'
    response = client.post('/api/export', json={'ids': [ident, ident]})
    assert response.status_code == 200
    assert response.text.startswith('\r\n'.join(INFO_ROWS + [HEADER_ROW]) + '\r\n')
    rows = list(csv.reader(io.StringIO(response.text)))
    assert len(rows) == 6 and len(rows[-1]) == 11
    assert rows[-1][0] == 'Draft' and rows[-1][5] == '12.34'
    assert rows[-1][3] == 'Test, Player'
    assert TestClient(create_app(root)).get('/api/cards').json()[0]['state'] == 'exported'


def test_partial_upload_retry_and_no_duplicate_front(workspace, monkeypatch):
    client, root = workspace
    front, back = imported(client, 2)
    ident = front['id']
    client.post(f'/api/cards/{ident}/pair', json={'back_id': back['id']})
    calls = []
    def upload(path):
        calls.append(path.name)
        if path.name == back['front']:
            raise RuntimeError('sensitive credentials must not appear')
        return 'https://example.com/front.jpg'
    monkeypatch.setattr('backend.services.upload', upload)
    result = client.post(f'/api/cards/{ident}/upload').json()
    assert result['state'] == 'failed' and result['front_url']
    assert 'sensitive' not in result['error']
    monkeypatch.setattr('backend.services.upload', lambda path: calls.append(path.name) or 'https://example.com/back.jpg')
    client.post(f'/api/cards/{ident}/upload')
    assert calls.count(front['front']) == 1


def test_extraction_never_sets_price(workspace, monkeypatch):
    client, _ = workspace
    card = imported(client)[0]
    fields = dict(player='Player', set_name='2024 Set', year=2024, team='', parallel='', card_number='1',
                  print_run=None, is_rookie=True, is_auto=False, is_relic=False, images_match=False, warnings=['Check number'])
    monkeypatch.setattr('backend.services.extract', lambda paths: (fields.copy(), {'input_tokens': 10}))
    result = client.post(f"/api/cards/{card['id']}/extract").json()
    assert result['listing']['price'] is None and result['listing']['reviewed'] is False
    assert '2024 2024' not in result['listing']['title']
    assert len(result['warnings']) == 2
    assert result['usage']['input_tokens'] == 10


def test_restart_recovers_processing(workspace):
    client, root = workspace
    card = imported(client)[0]
    card['state'] = 'processing'
    with sqlite3.connect(root / 'inventory.sqlite3') as conn:
        conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), card['id']))
    result = TestClient(create_app(root)).get('/api/cards').json()[0]
    assert result['state'] == 'failed'


def test_legacy_contract_and_title():
    source = ast.parse(Path('ebay_draft_lister.py').read_text(encoding='utf-8'))
    constants = {n.targets[0].id: ast.literal_eval(n.value) for n in source.body
                 if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ('HEADER_ROW', 'INFO_ROWS')}
    assert constants == {'HEADER_ROW': HEADER_ROW, 'INFO_ROWS': INFO_ROWS}
    title = build_title(dict(player='Jane Player', year=2024, set_name='2024 Topps Chrome', parallel='Gold', is_rookie=True, team='X'*100))
    assert len(title) <= 80 and title.startswith('Jane Player 2024 Topps Chrome') and 'RC' in title


def test_full_resolution_upload_and_smaller_vision(tmp_path):
    from backend.services import jpeg
    path = tmp_path / 'card.png'
    Image.new('RGB', (2000, 2500)).save(path)
    assert Image.open(io.BytesIO(jpeg(path))).size == (2000, 2500)
    assert max(Image.open(io.BytesIO(jpeg(path, vision=True))).size) == 1568


def test_heic_conversion(tmp_path):
    import pillow_heif
    from backend.services import jpeg
    path = tmp_path / 'iphone.heic'
    pillow_heif.from_pillow(Image.new('RGB', (80, 120), 'blue')).save(path)
    result = Image.open(io.BytesIO(jpeg(path)))
    assert result.format == 'JPEG' and result.size == (80, 120)


def test_openai_structured_contract(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend import services
    from backend.models import Extraction
    path = tmp_path / 'card.jpg'
    path.write_bytes(image())
    captured = {}
    class Client:
        def __init__(self, **kwargs):
            captured['client'] = kwargs
            self.responses = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def parse(self, **kwargs):
            captured['request'] = kwargs
            return SimpleNamespace(output_parsed=Extraction(player='P', set_name='S', year=None, team='',
                parallel='', card_number='', print_run=None, is_rookie=False, is_auto=False,
                is_relic=False, images_match=None, warnings=[]), usage=None)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(services, 'OpenAI', Client)
    result, usage = services.extract([path])
    assert result['player'] == 'P'
    assert captured['request']['text_format'] is Extraction
    assert captured['request']['store'] is False
    assert captured['request']['input'][0]['content'][1]['image_url'].startswith('data:image/jpeg;base64,')


def test_failed_reextraction_revokes_review(workspace, monkeypatch):
    client, _ = workspace
    card = imported(client)[0]
    client.put(f"/api/cards/{card['id']}", json=card['listing'] | {'reviewed': True})
    def fail(paths): raise RuntimeError('network')
    monkeypatch.setattr('backend.services.extract', fail)
    result = client.post(f"/api/cards/{card['id']}/extract").json()
    assert result['state'] == 'failed' and result['listing']['reviewed'] is False


def test_processing_rejects_edits_and_duplicate_work(workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    client, _ = workspace
    card = imported(client)[0]
    started, release = Event(), Event()
    def upload(path):
        started.set()
        assert release.wait(5)
        return 'https://example.com/photo.jpg'
    monkeypatch.setattr('backend.services.upload', upload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f"/api/cards/{card['id']}/upload")
        try:
            assert started.wait(5)
            assert client.put(f"/api/cards/{card['id']}", json=card['listing']).status_code == 409
            assert client.post(f"/api/cards/{card['id']}/upload").status_code == 409
            assert client.post('/api/export', json={'ids': [card['id']]}).status_code == 400
        finally:
            release.set()
        assert pending.result().status_code == 200


def test_cloudinary_retries_jpeg_payload(tmp_path, monkeypatch):
    from backend import services
    path = tmp_path / 'input.png'
    Image.new('RGB', (100, 200)).save(path)
    calls = []
    class Reply:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {'secure_url': 'https://example.com/photo.jpg'}
    def post(url, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise services.requests.Timeout()
        return Reply()
    monkeypatch.setenv('CLOUDINARY_CLOUD_NAME', 'test')
    monkeypatch.setenv('CLOUDINARY_UPLOAD_PRESET', 'test')
    monkeypatch.setattr(services.requests, 'post', post)
    monkeypatch.setattr(services.time, 'sleep', lambda seconds: None)
    assert services.upload(path) == 'https://example.com/photo.jpg'
    assert len(calls) == 3
    name, payload, mime = calls[0]['files']['file']
    assert name == 'input.jpg' and mime == 'image/jpeg'
    assert Image.open(io.BytesIO(payload)).size == (100, 200)
