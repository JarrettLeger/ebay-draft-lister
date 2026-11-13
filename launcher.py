"""One local server, one browser window; no Node process required after build."""
import socket
import threading
import time
import webbrowser

import uvicorn


def open_when_ready():
    for _ in range(60):
        try:
            with socket.create_connection(('127.0.0.1', 8765), timeout=0.2):
                webbrowser.open('http://127.0.0.1:8765')
                return
        except OSError:
            time.sleep(0.25)


if __name__ == '__main__':
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 8765))
        except OSError:
            raise SystemExit('Port 8765 is already in use. Close the other lister window/server first.')
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run('backend.app:app', host='127.0.0.1', port=8765, workers=1)
