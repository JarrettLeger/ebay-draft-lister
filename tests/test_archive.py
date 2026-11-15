import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.archive import slug
from test_sync_settings import photo


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    data = tmp_path / 'data'
    client = TestClient(create_app(data))
    client.put('/api/config', json={'source_folder': str(source)})
    for name in ('Card_front.jpg', 'Card_back.jpg', 'Other.jpg'):
        photo(source / name)
    client.post('/api/sync', json={'pairing': 'named'})
    card = next(c for c in client.get('/api/cards').json() if c['back'])
    monkeypatch.setattr('backend.services.upload', lambda path: 'https://example.com/photo.jpg')
    listing = card['listing'] | dict(player='Jane Player', year=2024, set_name='Test Set',
        title='Jane Player 2024 Test Set', description='See photos', price='15.00', reviewed=True)
    client.put(f"/api/cards/{card['id']}", json=listing)
    client.post(f"/api/cards/{card['id']}/upload")
    return client, source, data, card


def export(client, card):
    return client.post('/api/export', json={'ids': [card['id']]})


def test_export_archives_only_selected_after_saving_csv(inventory, monkeypatch):
    from backend import archive
    client, source, data, card = inventory
    actual = archive.move_no_replace
    def checked_move(origin, target):
        csvs = list((data / 'exports').glob('*.csv'))
        assert len(csvs) == 1 and 'Jane Player' in csvs[0].read_text()
        return actual(origin, target)
    monkeypatch.setattr(archive, 'move_no_replace', checked_move)
    response = export(client, card)
    assert response.status_code == 200 and response.headers['X-Archive-Warnings'] == '0'
    assert not (source / 'Card_front.jpg').exists() and not (source / 'Card_back.jpg').exists()
    assert (source / 'Other.jpg').is_file()
    names = {p.name for p in (source / 'Done').iterdir()}
    base = f'Jane_Player_2024_Test_Set_{card["id"][:8]}'
    assert names == {base + '_front.jpg', base + '_back.jpg'}
    assert len(list((data / 'photos').iterdir())) == 3
    saved = client.get('/api/exports/' + response.headers['X-Export-ID'])
    assert saved.content == response.content
    assert client.get('/api/exports/not-a-valid-id').status_code == 404


def test_repeated_export_and_restart_do_not_move_again(inventory):
    client, source, data, card = inventory
    assert export(client, card).status_code == 200
    photo(source / 'Card_front.jpg', 'blue')  # A new file using the old name is not the archived photo.
    restarted = TestClient(create_app(data))
    assert export(restarted, card).headers['X-Archive-Warnings'] == '0'
    assert len(list((source / 'Done').iterdir())) == 2
    assert (source / 'Card_front.jpg').is_file()


def test_filename_collision_never_overwrites(inventory):
    client, source, _, card = inventory
    done = source / 'Done'
    done.mkdir()
    occupied = done / f'Jane_Player_2024_Test_Set_{card["id"][:8]}_front.jpg'
    occupied.write_bytes(b'existing-photo')
    response = export(client, card)
    assert response.headers['X-Archive-Warnings'] == '0'
    assert occupied.read_bytes() == b'existing-photo'
    assert occupied.with_stem(occupied.stem + '_2').is_file()


def test_changed_original_is_left_untouched(inventory):
    client, source, _, card = inventory
    photo(source / 'Card_front.jpg', 'blue')
    before = (source / 'Card_front.jpg').read_bytes()
    response = export(client, card)
    assert response.status_code == 200 and response.headers['X-Archive-Warnings'] == '1'
    assert (source / 'Card_front.jpg').read_bytes() == before
    saved = next(c for c in client.get('/api/cards').json() if c['id'] == card['id'])
    assert 'changed since import' in saved['archive_warnings'][0]
    assert saved['state'] == 'exported'


def test_failed_csv_save_does_not_move_any_originals(inventory, monkeypatch):
    client, source, data, card = inventory
    def fail(_): raise OSError('disk failure')
    monkeypatch.setattr('backend.app.os.fsync', fail)
    with pytest.raises(OSError): export(client, card)
    assert (source / 'Card_front.jpg').is_file() and (source / 'Card_back.jpg').is_file()
    assert not (source / 'Done').exists()
    assert next(c for c in client.get('/api/cards').json() if c['id'] == card['id'])['state'] == 'ready'


def test_move_failure_can_be_retried(inventory, monkeypatch):
    from backend import archive
    client, source, _, card = inventory
    actual = archive.move_no_replace
    def fail(source, target): raise PermissionError('locked')
    monkeypatch.setattr(archive, 'move_no_replace', fail)
    assert export(client, card).headers['X-Archive-Warnings'] == '2'
    assert (source / 'Card_front.jpg').is_file() and (source / 'Card_back.jpg').is_file()
    monkeypatch.setattr(archive, 'move_no_replace', actual)
    assert export(client, card).headers['X-Archive-Warnings'] == '0'
    assert len(list((source / 'Done').iterdir())) == 2


def test_crash_after_rename_is_recovered_without_duplicate(inventory, monkeypatch):
    from backend import archive
    client, source, data, card = inventory
    actual = archive.move_no_replace
    def interrupted(origin, target):
        actual(origin, target)
        raise OSError('interrupted after rename')
    monkeypatch.setattr(archive, 'move_no_replace', interrupted)
    assert export(client, card).headers['X-Archive-Warnings'] == '2'
    assert len(list((source / 'Done').iterdir())) == 2
    monkeypatch.setattr(archive, 'move_no_replace', actual)
    restarted = TestClient(create_app(data))
    assert export(restarted, card).headers['X-Archive-Warnings'] == '0'
    assert len(list((source / 'Done').iterdir())) == 2


def test_blocked_export_leaves_source_files_in_place(inventory):
    client, source, _, card = inventory
    saved = next(c for c in client.get('/api/cards').json() if c['id'] == card['id'])
    client.put(f"/api/cards/{card['id']}", json=saved['listing'] | {'reviewed': False})
    assert export(client, card).status_code == 400
    assert (source / 'Card_front.jpg').is_file() and (source / 'Card_back.jpg').is_file()
    assert not (source / 'Done').exists()
    assert '/' not in slug('../../Jane / Player')
