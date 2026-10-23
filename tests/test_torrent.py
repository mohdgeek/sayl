"""اختبار التورنت محليًا: جلسة تزرع ملفًا، و TorrentDownload ينزّله منها مباشرة (بلا إنترنت)."""
import os
import time

import pytest

lt = pytest.importorskip("libtorrent")

from sayl import torrent
from sayl.engine import COMPLETED, ERROR, PAUSED


def _make_torrent(tmp_path, files):
    root = tmp_path / "seed" / "data"
    root.mkdir(parents=True)
    for name, size in files.items():
        (root / name).write_bytes(os.urandom(size))
    fs = lt.file_storage()
    lt.add_files(fs, str(root))
    t = lt.create_torrent(fs)
    lt.set_piece_hashes(t, str(root.parent))
    data = lt.bencode(t.generate())
    tf = tmp_path / "data.torrent"
    tf.write_bytes(data)
    return tf, root


@pytest.fixture
def seeder(tmp_path):
    tf, root = _make_torrent(tmp_path, {"a.bin": 3_000_000, "b.bin": 500_000})
    ses = lt.session({"listen_interfaces": "127.0.0.1:0", "enable_dht": False,
                      "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False})
    p = lt.add_torrent_params()
    p.ti = lt.torrent_info(str(tf))
    p.save_path = str(root.parent)
    ses.add_torrent(p)
    time.sleep(1.0)
    yield ses, tf, root
    del ses


def _leecher_session():
    torrent.reset_session()
    torrent._rate = 0
    torrent._ses = lt.session({"listen_interfaces": "127.0.0.1:0", "enable_dht": False,
                               "enable_lsd": False, "enable_upnp": False,
                               "enable_natpmp": False,
                               "allow_multiple_connections_per_ip": True,
                               "ignore_limits_on_local_network": False})


def _wait(d, pred, secs=40):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def test_helpers():
    assert torrent.is_torrent_url("magnet:?xt=urn:btih:ABC")
    assert torrent.is_torrent_url("https://x.org/a/b.torrent?x=1")
    assert torrent.is_torrent_url(r"C:\Users\me\f.torrent")
    assert not torrent.is_torrent_url("https://x.org/a.zip")
    assert torrent.magnet_hash("magnet:?xt=urn:btih:AbCdEf&dn=x") == "abcdef"
    assert torrent.magnet_name("magnet:?xt=urn:btih:a&dn=My%20File") == "My File"


def test_download_from_local_peer(tmp_path, seeder):
    ses, tf, root = seeder
    port = ses.listen_port()
    _leecher_session()
    out = tmp_path / "out"
    d = torrent.TorrentDownload(str(tf), str(out))
    d.test_peers = [("127.0.0.1", port)]
    d.start()
    assert _wait(d, lambda: d.state in (COMPLETED, ERROR)), (d.state, d.error, d.progress)
    assert d.state == COMPLETED, d.error
    assert d.filename == "data" and d.size >= 3_500_000
    for n in ("a.bin", "b.bin"):
        assert (out / "data" / n).read_bytes() == (root / n).read_bytes()
    assert d.progress == 1.0
    assert os.path.isfile(d.torrent_file)
    os.remove(d.torrent_file)


def test_pause_resume_and_cancel(tmp_path, seeder):
    ses, tf, root = seeder
    port = ses.listen_port()
    _leecher_session()
    torrent.apply_limit(400_000)            # بطيء كي نلحق بالإيقاف
    out = tmp_path / "out2"
    d = torrent.TorrentDownload(str(tf), str(out))
    d.test_peers = [("127.0.0.1", port)]
    d.start()
    assert _wait(d, lambda: d.downloaded > 200_000), (d.state, d.error)
    d.pause()
    d.wait(10)
    assert d.state == PAUSED
    got = d.downloaded
    assert got < 3_500_000
    torrent.apply_limit(0)
    d.state = torrent.QUEUED
    d.start()
    assert _wait(d, lambda: d.state in (COMPLETED, ERROR)), (d.state, d.error)
    assert d.state == COMPLETED, d.error
    assert (out / "data" / "a.bin").read_bytes() == (root / "a.bin").read_bytes()
    d.cancel(delete_files=False)
    d.wait(5)


def test_bad_torrent_file(tmp_path):
    _leecher_session()
    bad = tmp_path / "bad.torrent"
    bad.write_bytes(b"not a torrent")
    d = torrent.TorrentDownload(str(bad), str(tmp_path / "o"))
    d.start()
    assert _wait(d, lambda: d.state == ERROR, 10)
    assert "تالف" in d.error


def test_magnet_metadata_and_manager(tmp_path, seeder):
    """magnet: يجلب البيانات من القرين ثم يحمّل؛ والمدير يتعرّف على الروابط والحفظ والتحميل من الحالة."""
    from sayl.config import Config
    from sayl.manager import DownloadManager, DuplicateError
    ses, tf, root = seeder
    port = ses.listen_port()
    _leecher_session()
    ti = lt.torrent_info(str(tf))
    magnet = lt.make_magnet_uri(ti)
    cfg = Config(path=str(tmp_path / "cfg.json")) if "path" in Config.__init__.__code__.co_varnames else Config()
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    torrent.TorrentDownload.test_peers = [("127.0.0.1", port)]
    try:
        d = m.add(magnet, dest_dir=str(tmp_path / "m"))
        assert d.kind == "torrent"
        with pytest.raises(DuplicateError):
            m.add(magnet + "&dn=other")
        m.save()
        d.start()
        assert _wait(d, lambda: d.state in (COMPLETED, ERROR)), (d.state, d.error, d.stage)
        assert d.state == COMPLETED, d.error
        assert (tmp_path / "m" / "data" / "a.bin").read_bytes() == (root / "a.bin").read_bytes()
        m.save()
        m2 = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
        d2 = m2.items[d.id]
        assert d2.kind == "torrent" and d2.state == COMPLETED and d2.filename == "data"
        with pytest.raises(ValueError):
            m.add("magnet:?dn=nohash")
        os.remove(d.torrent_file)
    finally:
        torrent.TorrentDownload.test_peers = []


def test_server_forwards_torrent(tmp_path):
    """نسخة ثانية من سيل تمرّر رابط magnet إلى النسخة العاملة عبر الخادم المحلي."""
    from sayl import setup_tools
    from sayl.config import Config
    from sayl.manager import DownloadManager
    from sayl.server import start_server
    cfg = Config(str(tmp_path / "c.json"))
    cfg["port"] = 0
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    got = []
    srv, _ = start_server(m, cfg, on_confirm=lambda u, h, k, o: got.append((u, k)))
    try:
        cfg["port"] = srv.port
        magnet = "magnet:?xt=urn:btih:" + "ab" * 20
        assert setup_tools.forward_to_running(cfg, magnet)
        assert got == [(magnet, "torrent")]
        assert not setup_tools.forward_to_running({"port": srv.port, "token": "bad"}, magnet)
    finally:
        srv.stop()


def test_select_files(tmp_path, seeder):
    """استبعاد ملف من التورنت: لا يُنزَّل، ويُحفظ الاختيار."""
    from sayl.config import Config
    from sayl.manager import DownloadManager
    ses, tf, root = seeder
    files = torrent.read_files(str(tf))
    assert [f["path"] for f in files] == ["data/a.bin", "data/b.bin"]
    skip_a = [f["index"] for f in files if f["path"].endswith("a.bin")]
    _leecher_session()
    m = DownloadManager(Config(str(tmp_path / "c.json")), state_path=str(tmp_path / "s.json"), autostart=False)
    torrent.TorrentDownload.test_peers = [("127.0.0.1", ses.listen_port())]
    try:
        d = m.add(str(tf), dest_dir=str(tmp_path / "sel"), skip=skip_a)
        assert d.skip == skip_a
        d.start()
        assert _wait(d, lambda: d.state in (COMPLETED, ERROR)), (d.state, d.error)
        assert d.state == COMPLETED, d.error
        assert (tmp_path / "sel" / "data" / "b.bin").read_bytes() == (root / "b.bin").read_bytes()
        a = tmp_path / "sel" / "data" / "a.bin"
        assert not a.exists() or a.stat().st_size == 0 or a.read_bytes() != (root / "a.bin").read_bytes()
        assert d.size < 1_000_000
        assert len(d.files()) == 2
        m.save()
        assert DownloadManager(m.config, state_path=str(tmp_path / "s.json"),
                               autostart=False).items[d.id].skip == skip_a
    finally:
        torrent.TorrentDownload.test_peers = []
