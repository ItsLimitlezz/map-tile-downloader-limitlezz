import socket
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog


def _pick_free_port(host, preferred_port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, preferred_port))
            return preferred_port
        except OSError:
            sock.bind((host, 0))
            return sock.getsockname()[1]


def _wait_for_server(host, port, timeout_seconds=20):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


class TileDownloaderWindow(QWebEngineView):
    def __init__(self, url, server_process):
        super().__init__()
        self._server_process = server_process
        self.setWindowTitle('MeshStudio Lite')
        self.resize(1280, 800)
        self.page().profile().downloadRequested.connect(self._on_download_requested)
        self.load(QUrl(url))

    def _on_download_requested(self, download):
        downloads_dir = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        suggested_name = download.downloadFileName() or 'download.bin'
        suggested_path = str(Path(downloads_dir) / suggested_name)
        target_path, _ = QFileDialog.getSaveFileName(
            self,
            'Save Download',
            suggested_path,
            'All files (*)',
        )

        if not target_path:
            download.cancel()
            return

        target = Path(target_path)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()

    def closeEvent(self, event):
        if self._server_process and self._server_process.poll() is None:
            self._server_process.terminate()
            try:
                self._server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server_process.kill()
        super().closeEvent(event)


def launch_qt_app(host='127.0.0.1', port=5000):
    bind_host = '127.0.0.1'
    ui_host = '127.0.0.1'
    selected_port = _pick_free_port(bind_host, port)

    if getattr(sys, 'frozen', False):
        command = [
            sys.executable,
            '--server-only',
            '--host',
            bind_host,
            '--port',
            str(selected_port),
        ]
    else:
        script_path = Path(__file__).resolve().with_name('TileDL.py')
        command = [
            sys.executable,
            str(script_path),
            '--server-only',
            '--host',
            bind_host,
            '--port',
            str(selected_port),
        ]

    server_process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_for_server(ui_host, selected_port):
        if server_process.poll() is None:
            server_process.terminate()
            try:
                server_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server_process.kill()
        print('Failed to start local server for Qt app.', file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    window = TileDownloaderWindow(f'http://{ui_host}:{selected_port}/', server_process)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(launch_qt_app())
