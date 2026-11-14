import threading

import pytest

from flats import embed, trips


class Response:
    status_code = 200
    headers = {'content-type': 'image/jpeg'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield b'123456'


def test_photo_fetch_restricts_host_redirects_and_size(monkeypatch, tmp_path):
    monkeypatch.setattr(embed.config, 'photo_dir', lambda: tmp_path)
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(embed.requests, 'get', get)
    for url in ('http://static.baza.farpost.ru/a', 'https://127.0.0.1/a', 'https://static.baza.farpost.ru.evil/a'):
        with pytest.raises(ValueError): embed._download(url)
    monkeypatch.setattr(embed, 'MAX_BYTES', 5)
    with pytest.raises(ValueError, match='too large'):
        embed._download('https://static.baza.farpost.ru/a')
    assert calls == [{'timeout': 20, 'stream': True, 'allow_redirects': False}]
    assert not list(tmp_path.iterdir())


def test_busy_job_does_not_block_another_request():
    lock = threading.RLock()
    ready, release = threading.Event(), threading.Event()
    def hold():
        with lock:
            ready.set()
            release.wait(3)
    thread = threading.Thread(target=hold)
    thread.start()
    assert ready.wait(1)
    try:
        with pytest.raises(BlockingIOError):
            trips._public({'lock': lock})
    finally:
        release.set()
        thread.join()
