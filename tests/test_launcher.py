import socket
from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI

from launcher import LocalServer, run_desktop


def test_server_owns_its_socket_and_stops_cleanly():
    app = FastAPI()
    stopped = []
    app.state.stop_background = lambda: stopped.append(True)
    @app.get('/')
    def health(): return {'ready': True}
    local = LocalServer(port=0, app_factory=lambda: app)
    try:
        url = local.start()
        assert requests.get(url, timeout=5).json() == {'ready': True}
        port = local.port
    finally:
        local.stop()
    assert stopped and not local.thread.is_alive()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))


def test_busy_port_never_attaches_to_or_loads_another_app():
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        local = LocalServer(port=occupied.getsockname()[1],
            app_factory=lambda: pytest.fail('Must not open inventory when port is occupied'))
        with pytest.raises(RuntimeError, match='already running'):
            local.start()
        assert local.socket is None


def test_failed_backend_start_releases_socket():
    def broken(): raise RuntimeError('startup failed')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    local = LocalServer(port=port, app_factory=broken)
    with pytest.raises(RuntimeError, match='startup failed'):
        local.start()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))


@pytest.mark.parametrize('failure', [False, True])
def test_desktop_lifecycle_and_close_guard(failure):
    calls = []
    class Local:
        def start(self):
            calls.append('start')
            return 'http://127.0.0.1:8765'
        def stop(self): calls.append('stop')
    class Event(list):
        def __iadd__(self, callback):
            self.append(callback)
            return self
    class Window:
        events = SimpleNamespace(closing=Event(), loaded=Event())
        def evaluate_js(self, code): return False
    class Webview:
        settings = {}
        def create_window(self, title, url, **kwargs):
            assert title == 'eBay Draft Lister' and url.startswith('http://127.0.0.1:')
            assert kwargs['min_size'] == (800, 600)
            self.window = Window()
            return self.window
        def start(self, **kwargs):
            assert kwargs['debug'] is False and kwargs['private_mode'] is True
            assert self.settings['ALLOW_DOWNLOADS'] is True
            assert self.settings['ALLOW_FILE_URLS'] is False
            assert self.window.events.closing[-1]() is False
            if failure: raise RuntimeError('renderer failed')
    if failure:
        with pytest.raises(RuntimeError, match='renderer failed'):
            run_desktop(Local(), Webview())
    else:
        run_desktop(Local(), Webview())
    assert calls == ['start', 'stop']
