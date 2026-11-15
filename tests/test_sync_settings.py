import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.settings import DEFAULT_FOLDER


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    data = tmp_path / 'inventory'
    client = TestClient(create_app(data))
    assert client.put('/api/config', json={'source_folder': str(source)}).status_code == 200
    return client, source, data


def photo(path, color='red'):
    Image.new('RGB', (30, 40), color).save(path)


def sync(client, pairing='consecutive'):
    response = client.post('/api/sync', json={'pairing': pairing})
    assert response.status_code == 200, response.text
    return response.json()


def test_default_folder_and_unreachable(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get('/api/config').json()['source_folder'] == DEFAULT_FOLDER
    client.put('/api/config', json={'source_folder': str(tmp_path / 'missing')})
    assert client.post('/api/sync', json={}).status_code == 400
    assert client.put('/api/config', json={'model': 'gpt-4.1-mini'}, headers={
        'host': '127.0.0.1:8766', 'origin': 'http://127.0.0.1:8766'}).status_code == 200
    assert client.put('/api/config', json={'api_key': 'do-not-save'}, headers={
        'host': '127.0.0.1:8766', 'origin': 'http://127.0.0.1:9999'}).status_code == 403


def test_sync_more_than_100_and_repeat_is_idempotent(workspace):
    client, source, data = workspace
    for i in range(103):
        photo(source / f'IMG_{i}.jpg')
    result = sync(client)
    assert result['imported'] == 103 and result['paired'] == 51
    assert len(client.get('/api/cards').json()) == 52
    repeated = sync(TestClient(create_app(data)))
    assert repeated['imported'] == 0 and repeated['skipped'] == 103 and repeated['paired'] == 0
    assert len(list(source.iterdir())) == 103
    assert len(list((data / 'photos').iterdir())) == 103


def test_filename_pairs_late_back_and_ignores_subfolders(workspace):
    client, source, _ = workspace
    photo(source / 'Player_front.jpg')
    (source / 'Done').mkdir()
    photo(source / 'Done' / 'old.jpg')
    (source / 'notes.txt').write_text('not a photo')
    assert sync(client, 'named')['imported'] == 1
    photo(source / 'Player_back.jpg')
    assert sync(client, 'named')['paired'] == 1
    card = client.get('/api/cards').json()[0]
    assert card['original'] == 'Player_front.jpg' and card['back_original'] == 'Player_back.jpg'
    assert not card['listing']['reviewed'] and card['warnings']
    client.post(f"/api/cards/{card['id']}/unpair")
    assert sync(client)['paired'] == 0  # Respect a deliberate user correction.


def test_natural_pairing_and_changed_file(workspace):
    client, source, _ = workspace
    for number in (1, 2, 10, 11):
        photo(source / f'photo{number}.jpg')
    assert sync(client)['paired'] == 2
    cards = client.get('/api/cards').json()
    assert {(c['original'], c['back_original']) for c in cards} == {('photo1.jpg','photo2.jpg'), ('photo10.jpg','photo11.jpg')}
    photo(source / 'photo1.jpg', 'blue')
    assert sync(client)['imported'] == 1
    assert len(client.get('/api/cards').json()) == 3


def test_corrupt_file_does_not_discard_good_files(workspace):
    client, source, data = workspace
    photo(source / 'good.jpg')
    (source / 'bad.jpg').write_bytes(b'not an image')
    result = sync(client)
    assert result['imported'] == 1 and len(result['errors']) == 1
    assert len(list((data / 'photos').iterdir())) == 1
    assert sync(client)['skipped'] == 1


def test_recognizes_picker_import_and_pairs_on_sync(workspace):
    client, source, _ = workspace
    photo(source / 'Card_front.jpg')
    photo(source / 'Card_back.jpg')
    for path in source.iterdir():
        assert client.post('/api/import', files=[('files', (path.name, path.read_bytes()))]).status_code == 200
    result = sync(client)
    assert result['imported'] == 0 and result['skipped'] == 2 and result['paired'] == 1
    assert len(client.get('/api/cards').json()) == 1


def test_secrets_encrypted_persistent_never_echoed(workspace, monkeypatch):
    client, _, data = workspace
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    key = 'test-secret-credential'
    response = client.put('/api/config', json={'api_key': key, 'cloud_name': 'test-cloud', 'upload_preset': 'test-preset'})
    assert response.status_code == 200, response.text
    assert response.json()['openai'] and response.json()['cloudinary']
    assert key not in response.text and 'test-preset' not in response.text
    stored = (data / 'settings.json').read_text()
    assert key not in stored and 'test-preset' not in stored
    restarted = TestClient(create_app(data))
    assert restarted.get('/api/config').json()['openai']
    restarted.put('/api/config', json={'api_key': '', 'model': 'gpt-4.1-mini'})
    assert restarted.get('/api/config').json()['openai']
    restarted.put('/api/config', json={'clear_api_key': True})
    assert not restarted.get('/api/config').json()['openai']
    invalid = client.put('/api/config', json={'api_key': key * 100})
    assert invalid.status_code == 422 and key not in invalid.text


def test_saved_connections_apply_to_processing(workspace, monkeypatch):
    from backend import services
    client, source, _ = workspace
    photo(source / 'single.jpg')
    sync(client, 'none')
    client.put('/api/config', json={'cloud_name': 'saved-cloud', 'upload_preset': 'saved-preset'})
    captured = []
    def upload(path):
        captured.append((services.setting('CLOUDINARY_CLOUD_NAME'), services.setting('CLOUDINARY_UPLOAD_PRESET')))
        return 'https://example.com/image.jpg'
    monkeypatch.setattr(services, 'upload', upload)
    ident = client.get('/api/cards').json()[0]['id']
    client.post(f'/api/cards/{ident}/upload')
    assert captured == [('saved-cloud', 'saved-preset')]
