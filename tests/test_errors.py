from types import SimpleNamespace

import pytest
import requests

from backend.services import safe_error, UserFacingError


@pytest.mark.parametrize('status,expected', [
    (401, 'rejected access'), (403, 'rejected access'), (404, 'model'),
    (429, 'credits/limits'), (400, 'structured-output'), (422, 'structured-output'),
    (503, 'temporarily unavailable')])
def test_provider_errors_are_actionable_without_echoing_secrets(status, expected):
    class Failure(Exception):
        status_code = status
    result = safe_error(Failure('secret-key-and-private-response'))
    assert expected in result
    assert 'secret-key' not in result and 'private-response' not in result


def test_network_errors_and_unknown_errors_do_not_leak_details():
    assert 'timed out' in safe_error(requests.Timeout('private data'))
    assert 'internet connection' in safe_error(requests.ConnectionError('secret proxy credentials'))
    assert 'private data' not in safe_error(RuntimeError('private data'))
    assert safe_error(UserFacingError('Enter your key in Settings')) == 'Enter your key in Settings'
    failure = requests.HTTPError('secret response')
    failure.response = SimpleNamespace(status_code=403)
    assert 'rejected access' in safe_error(failure)
