"""Native Windows window owning one local application server."""
import argparse
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import threading
import time
import webbrowser

from dotenv import load_dotenv
import uvicorn

ROOT = Path(__file__).resolve().parent


class LocalServer:
    def __init__(self, port=8765, app_factory=None):
        self.port = port
        self.app_factory = app_factory
        self.socket = None
        self.thread = None
        self.server = None
        self.app = None

    def start(self, timeout=20):
        self.socket = socket.socket()
        try:
            # Reserve the actual server socket, so a second launcher cannot race us.
            if os.name == 'nt':
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.socket.bind(('127.0.0.1', self.port))
            self.socket.listen(128)
        except OSError as exc:
            self.socket.close()
            self.socket = None
            raise RuntimeError('eBay Draft Lister is already running, or its port is in use. Close the existing app or its old command window, then try again.') from exc
        try:
            if self.app_factory is None:
                from backend.app import app
                self.app = app
            else:
                self.app = self.app_factory()
            self.port = self.socket.getsockname()[1]
            self.server = uvicorn.Server(uvicorn.Config(self.app, host='127.0.0.1', port=self.port,
                log_config=None, access_log=False, timeout_graceful_shutdown=10))
            self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, daemon=True)
            self.thread.start()
            deadline = time.monotonic() + timeout
            while not self.server.started:
                if not self.thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError('The local app could not start. Check data/logs/desktop.log for details.')
                time.sleep(0.05)
            return f'http://127.0.0.1:{self.port}'
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.app is not None:
            stop = getattr(self.app.state, 'stop_background', None)
            if stop:
                stop()
        if self.server:
            self.server.should_exit = True
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=15)
        if self.socket:
            self.socket.close()
            self.socket = None


def run_desktop(server=None, webview_module=None, hidden=False, on_ready=None):
    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError as exc:
            raise RuntimeError('The desktop window component is missing. Run setup-windows.cmd once, then open the app again.') from exc
    local = server or LocalServer()
    try:
        url = local.start()
        webview_module.settings['ALLOW_DOWNLOADS'] = True
        webview_module.settings['ALLOW_FILE_URLS'] = False
        window = webview_module.create_window('eBay Draft Lister', url, width=1380, height=940,
            min_size=(800, 600), background_color='#f5f7f5', text_select=True, hidden=hidden)

        close_state = {'allowed': False, 'pending': False}
        close_lock = threading.Lock()

        def request_close():
            try:
                allowed = window.evaluate_js('typeof window.confirmAppClose === "function" ? window.confirmAppClose() : true') is not False
            except Exception:
                allowed = True  # Always permit closing a broken/failed window.
            with close_lock:
                close_state.update(allowed=allowed, pending=False)
            if allowed:
                window.destroy()

        def can_close():
            with close_lock:
                if close_state['allowed']:
                    return True
                if not close_state['pending']:
                    close_state['pending'] = True
                    # Never synchronously evaluate JavaScript on the WinForms closing event:
                    # it blocks the UI thread needed to answer the JavaScript request.
                    threading.Thread(target=request_close, daemon=True).start()
            return False

        window.events.closing += can_close
        if on_ready:
            window.events.loaded += lambda: on_ready(window)
        webview_module.start(gui='edgechromium' if os.name == 'nt' else None,
            icon=str(ROOT / 'assets' / 'app.ico'), debug=False, private_mode=True)
    finally:
        local.stop()


def run_browser():
    local = LocalServer()
    try:
        webbrowser.open(local.start())
        while local.thread.is_alive():
            local.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        local.stop()


def main():
    parser = argparse.ArgumentParser(description='eBay Draft Lister desktop app')
    parser.add_argument('--browser', action='store_true', help='Use the optional browser mode')
    args = parser.parse_args()
    if os.name == 'nt':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MatchdayCards.eBayDraftLister.v2')
    os.chdir(ROOT)
    load_dotenv(ROOT / '.env')
    data = Path(os.getenv('LISTER_DATA_DIR', ROOT / 'data')).resolve()
    log_dir = data / 'logs'
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(log_dir / 'desktop.log',
            maxBytes=2_000_000, backupCount=3, encoding='utf-8')])
        if not (ROOT / 'frontend' / 'dist' / 'index.html').is_file():
            raise RuntimeError('The app interface has not been built. Run setup-windows.cmd first.')
        run_browser() if args.browser else run_desktop()
        return 0
    except Exception as exc:
        logging.exception('Desktop startup failed')
        message = str(exc) if isinstance(exc, RuntimeError) else 'Could not open the app. Run setup-windows.cmd and check that Microsoft Edge WebView2 Runtime is installed. Details are in data/logs/desktop.log.'
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, message, 'eBay Draft Lister', 0x10)
        else:
            print(message)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
