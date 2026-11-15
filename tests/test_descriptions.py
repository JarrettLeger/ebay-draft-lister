import csv
import io
import json
import sqlite3

import pytest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.descriptions import build_description, description_html
from test_app import imported


def test_card_details_produce_buyer_friendly_copy():
    text = build_description(dict(player='Timothy Weah', year=2026,
        set_name='Topps Stadium Club Chrome UEFA Champions League', team='Olympique de Marseille',
        parallel='Purple', card_number='26', print_run=75))
    assert text.startswith('Timothy Weah — Purple Parallel /75\n\n')
    assert '2026 Topps Stadium Club Chrome UEFA Champions League featuring Timothy Weah of Olympique de Marseille.' in text
    assert '- Print run: /75' in text and '- Card number: #26' in text
    assert 'condition and details' in text
    assert 'Questions about the card?' in text
    assert not any(claim in text.lower() for claim in ('mint', 'investment', 'fast shipping', 'on-card', 'guaranteed', 'rare'))


def test_missing_fields_do_not_invent_claims():
    text = build_description({'player': 'Player'})
    assert 'None' not in text and 'Base' not in text
    assert 'Print run' not in text and 'Autograph' not in text and 'Rookie' not in text
    assert 'Year' not in text


def test_year_and_parallel_not_duplicated():
    text = build_description(dict(player='Player', year=2026, set_name='2026 Topps',
        parallel='Purple Parallel /75', print_run=75, card_number='#26'))
    assert '2026 2026' not in text and 'Parallel Parallel' not in text
    assert '/75 /75' not in text and '##26' not in text


def test_flags_only_when_provided_and_no_autograph_type_claim():
    text = build_description(dict(player='Player', is_rookie=True, is_auto=True, is_relic=True))
    assert '- Rookie card (RC)' in text and '- Autograph card' in text and '- Relic card' in text
    assert 'On-Card' not in text and 'authentic' not in text.lower()


def test_html_is_escaped_and_layout_is_preserved():
    text = 'Player <script>alert(1)</script> & Team\n\nCard description\nsecond line\n\n- Purple\n- /75'
    rendered = description_html(text)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert '&amp; Team' in rendered and '<strong>' in rendered
    assert 'Card description<br>second line' in rendered
    assert '<ul><li>Purple</li><li>/75</li></ul>' in rendered


def test_generation_is_preview_only_and_manual_price_is_untouched(tmp_path):
    client = TestClient(create_app(tmp_path))
    card = imported(client)[0]
    listing = card['listing'] | dict(player='Player', parallel='Gold', print_run=50,
        description='My custom description', price='12.50', reviewed=True)
    client.put(f"/api/cards/{card['id']}", json=listing)
    response = client.post('/api/description', json=listing)
    assert response.status_code == 200 and 'Gold Parallel /50' in response.json()['description']
    persisted = client.get('/api/cards').json()[0]
    assert persisted['listing']['description'] == 'My custom description'
    assert persisted['listing']['price'] == '12.50' and persisted['listing']['reviewed'] is True


def test_export_formats_description_without_changing_csv_columns(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    card = imported(client)[0]
    description = build_description(dict(player='Player', parallel='Purple', print_run=75))
    client.put(f"/api/cards/{card['id']}", json=card['listing'] | dict(player='Player', set_name='Set',
        title='Player Purple /75', description=description, price='10.00', reviewed=True))
    monkeypatch.setattr('backend.services.upload', lambda path: 'https://example.com/photo.jpg')
    client.post(f"/api/cards/{card['id']}/upload")
    response = client.post('/api/export', json={'ids': [card['id']]})
    row = list(csv.reader(io.StringIO(response.text)))[-1]
    assert len(row) == 11 and '<strong>Player' in row[9] and '<li>Print run: /75</li>' in row[9]
    assert row[5] == '10.00'


def test_bulk_descriptions_save_all_and_keep_prices(tmp_path):
    client = TestClient(create_app(tmp_path))
    cards = imported(client, 3)
    for i, card in enumerate(cards):
        client.put(f"/api/cards/{card['id']}", json=card['listing'] | dict(player=f'Player {i}',
            description='Old copy', price='15.25', reviewed=True))
    ids = [cards[0]['id'], cards[1]['id'], cards[0]['id']]
    response = client.post('/api/descriptions', json={'ids': ids})
    assert response.status_code == 200 and response.json()['updated'] == 2
    saved = {c['id']: c for c in TestClient(create_app(tmp_path)).get('/api/cards').json()}
    for card in cards[:2]:
        listing = saved[card['id']]['listing']
        assert listing['description'] != 'Old copy' and not listing['reviewed']
        assert listing['price'] == '15.25'
    assert saved[cards[2]['id']]['listing']['description'] == 'Old copy'


@pytest.mark.parametrize('blocked', ['exported', 'queued', 'processing', 'missing'])
def test_bulk_description_validation_does_not_partially_overwrite(tmp_path, blocked):
    client = TestClient(create_app(tmp_path))
    cards = imported(client, 2)
    ident = cards[1]['id']
    if blocked == 'missing':
        ident = 'not-present'
    else:
        card = cards[1] | {'state': blocked}
        with sqlite3.connect(tmp_path / 'inventory.sqlite3') as conn:
            conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), ident))
    response = client.post('/api/descriptions', json={'ids': [cards[0]['id'], ident]})
    assert response.status_code == (404 if blocked == 'missing' else 409)
    saved = {c['id']: c for c in client.get('/api/cards').json()}
    assert saved[cards[0]['id']]['listing']['description'] == ''
