import json
import time
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import services
from backend.app import create_app
from backend.models import Extraction
from backend.settings import Settings
from test_app import image


def fields():
    return dict(player='Player', set_name='Test Set', year=2024, team='', parallel='',
                card_number='1', print_run=None, is_rookie=False, is_auto=False,
                is_relic=False, images_match=True, warnings=[])


def test_legacy_settings_default_to_openai(tmp_path, monkeypatch):
    monkeypatch.delenv('LISTER_AI_PROVIDER', raising=False)
    (tmp_path / 'settings.json').write_text(json.dumps({'model': 'existing-openai-model'}))
    settings = Settings(tmp_path)
    assert settings.public()['provider'] == 'openai'
    assert settings.public()['model'] == 'existing-openai-model'
    assert settings.public()['claude_model'] == 'claude-sonnet-4-6'


def test_provider_credentials_are_independent_and_hidden(tmp_path, monkeypatch):
    monkeypatch.delenv('LISTER_AI_PROVIDER', raising=False)
    client = TestClient(create_app(tmp_path))
    response = client.put('/api/config', json={'provider': 'claude', 'api_key': 'openai-secret-test',
        'claude_api_key': 'claude-secret-test', 'model': 'openai-model', 'claude_model': 'claude-model'})
    assert response.status_code == 200
    public = response.json()
    assert public['provider'] == 'claude' and public['ready'] and public['model'] == 'claude-model'
    assert 'secret-test' not in response.text
    assert 'secret-test' not in (tmp_path / 'settings.json').read_text()
    config = Settings(tmp_path)
    assert config.runtime()['ANTHROPIC_API_KEY'] == 'claude-secret-test'
    config.update({'provider': 'openai', 'claude_api_key': ''})
    assert config.public()['model'] == 'openai-model' and config.public()['ready']
    config.update({'clear_claude_api_key': True})
    assert not config.public()['claude'] and config.public()['openai']
    assert client.put('/api/config', json={'provider': 'other'}).status_code == 422
    oversized = client.put('/api/config', json={'claude_api_key': 'secret-test' * 200})
    assert oversized.status_code == 422 and 'secret-test' not in oversized.text


@pytest.mark.parametrize('stop_reason,parsed,ok', [
    ('end_turn', True, True), ('refusal', False, False), ('max_tokens', True, False), ('end_turn', False, False)])
def test_claude_structured_vision_contract(tmp_path, monkeypatch, stop_reason, parsed, ok):
    path = tmp_path / 'photo.jpg'
    path.write_bytes(image())
    captured = {}
    class Client:
        def __init__(self, **kwargs):
            captured['client'] = kwargs
            self.messages = self
        def __enter__(self): return self
        def __exit__(self, *args): captured['closed'] = True
        def parse(self, **kwargs):
            captured['request'] = kwargs
            return SimpleNamespace(stop_reason=stop_reason, parsed_output=Extraction(**fields()) if parsed else None,
                usage=SimpleNamespace(model_dump=lambda: {'input_tokens': 11, 'output_tokens': 20}))
    monkeypatch.setattr(services, 'Anthropic', Client)
    monkeypatch.setattr(services, 'OpenAI', lambda **kwargs: pytest.fail('OpenAI must not receive Claude photos'))
    with services.configuration({'LISTER_AI_PROVIDER': 'claude', 'ANTHROPIC_API_KEY': 'claude-test', 'ANTHROPIC_MODEL': 'test-model'}):
        if ok:
            result, usage = services.extract([path, path])
            assert result == fields() and usage['input_tokens'] == 11
        else:
            with pytest.raises(RuntimeError): services.extract([path, path])
    request = captured['request']
    assert captured['closed']
    assert captured['client'] == {'api_key': 'claude-test', 'timeout': 60, 'max_retries': 2}
    assert request['model'] == 'test-model' and request['output_format'] is Extraction
    assert request['max_tokens'] == 2048
    content = request['messages'][0]['content']
    assert len(content) == 3 and content[1]['source']['media_type'] == 'image/jpeg'
    assert content[1]['source']['type'] == 'base64'


def test_selected_provider_requires_its_own_key(tmp_path, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    client = TestClient(create_app(tmp_path))
    card = client.post('/api/import', files=[('files', ('card.jpg', image()))]).json()[0]
    client.put('/api/config', json={'provider': 'claude', 'api_key': 'only-openai-key', 'clear_claude_api_key': True})
    assert not client.get('/api/config').json()['ready']
    assert client.post('/api/jobs', json={'operation': 'extract', 'ids': [card['id']]}).status_code == 400
    monkeypatch.setattr(services, 'OpenAI', lambda **kwargs: pytest.fail('No fallback permitted'))
    result = client.post(f"/api/cards/{card['id']}/extract").json()
    assert result['state'] == 'failed'
    assert result['listing']['price'] is None


def test_claude_sdk_parses_mock_http_response(tmp_path, monkeypatch):
    import httpx2
    from anthropic import Anthropic
    path = tmp_path / 'photo.jpg'
    path.write_bytes(image())
    captured = {}
    def respond(request):
        captured.update(json.loads(request.content))
        return httpx2.Response(200, json={
            'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-4-6',
            'content': [{'type': 'text', 'text': json.dumps(fields())}],
            'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 12, 'output_tokens': 30}})
    def client(**kwargs):
        return Anthropic(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(respond)))
    monkeypatch.setattr(services, 'Anthropic', client)
    with services.configuration({'LISTER_AI_PROVIDER': 'claude', 'ANTHROPIC_API_KEY': 'test-key', 'ANTHROPIC_MODEL': 'claude-sonnet-4-6'}):
        result, usage = services.extract([path])
    assert result == fields() and usage['input_tokens'] == 12
    assert captured['output_config']['format']['type'] == 'json_schema'
    assert captured['messages'][0]['content'][1]['source']['media_type'] == 'image/jpeg'


def test_batch_pins_provider_model_and_key(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    client.put('/api/config', json={'provider': 'claude', 'claude_api_key': 'starting-claude-key',
        'claude_model': 'starting-model', 'api_key': 'other-openai-key'})
    cards = client.post('/api/import', files=[('files', (f'{i}.jpg', image())) for i in range(2)]).json()
    started, release = Event(), Event()
    calls = []
    def extract(paths):
        calls.append((services.setting('LISTER_AI_PROVIDER'), services.setting('ANTHROPIC_MODEL'), services.setting('ANTHROPIC_API_KEY')))
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return fields(), {'input_tokens': 10}
    monkeypatch.setattr(services, 'extract', extract)
    response = client.post('/api/jobs', json={'operation': 'extract', 'ids': [c['id'] for c in cards]})
    assert response.json()['provider'] == 'claude' and response.json()['model'] == 'starting-model'
    try:
        assert started.wait(5)
        client.put('/api/config', json={'provider': 'openai', 'claude_model': 'changed-model', 'claude_api_key': 'changed-key'})
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get('/api/jobs').json()[0]
        if job['state'] == 'completed': break
        time.sleep(0.01)
    assert job['state'] == 'completed'
    assert calls == [('claude', 'starting-model', 'starting-claude-key')] * 2
    results = client.get('/api/cards').json()
    assert all(c['extraction_provider'] == 'claude' and c['extraction_model'] == 'starting-model' for c in results)
    assert all(c['listing']['price'] is None and not c['listing']['reviewed'] for c in results)
    assert 'starting-claude-key' not in json.dumps(job) + json.dumps(results)
