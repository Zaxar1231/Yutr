import hashlib
import io
import zipfile

import pytest

from autodub.updater.install import Installation, UpdateError, download, parse_checksums


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_update_cycle_and_rollback(tmp_path) -> None:
    inst = Installation(tmp_path)
    a = tmp_path / "a.zip"
    sha_a = make_zip(a, {"src/autodub/__init__.py": "v010"})
    inst.stage(a, "0.1.0", sha_a)
    inst.switch("0.1.0")
    inst.mark_healthy("0.1.0")
    assert inst.current == "0.1.0" and inst.previous is None

    b = tmp_path / "b.zip"
    sha_b = make_zip(b, {"src/autodub/__init__.py": "v011"})
    inst.stage(b, "0.1.1", sha_b)
    inst.switch("0.1.1")
    assert inst.current == "0.1.1" and inst.previous == "0.1.0"
    assert not inst.healthy("0.1.1")
    # old version is untouched (it is the backup)
    assert (inst.version_dir("0.1.0") / "src/autodub/__init__.py").read_text() == "v010"

    # 0.1.1 never reported a healthy start -> rollback
    assert inst.rollback() == "0.1.0"
    assert inst.current == "0.1.0" and "0.1.1" in inst.bad()


def test_checksum_mismatch_is_rejected(tmp_path) -> None:
    inst = Installation(tmp_path)
    a = tmp_path / "a.zip"
    make_zip(a, {"x": "y"})
    with pytest.raises(UpdateError):
        inst.stage(a, "0.2.0", "0" * 64)
    assert not inst.version_dir("0.2.0").exists() and not a.exists()


def test_zip_slip_is_rejected(tmp_path) -> None:
    a = tmp_path / "evil.zip"
    sha = make_zip(a, {"../../evil.txt": "x"})
    with pytest.raises(UpdateError):
        Installation(tmp_path / "inst").stage(a, "0.3.0", sha)


def test_switch_to_missing_version_fails(tmp_path) -> None:
    with pytest.raises(UpdateError):
        Installation(tmp_path).switch("9.9.9")


def test_rollback_without_previous(tmp_path) -> None:
    assert Installation(tmp_path).rollback() is None


def test_parse_checksums() -> None:
    text = f"{'a' * 64}  Autodub-0.1.1-update.zip\n{'B' * 64} *Autodub-Setup.exe\nnoise\n"
    assert parse_checksums(text) == {"Autodub-0.1.1-update.zip": "a" * 64, "Autodub-Setup.exe": "b" * 64}


class FakeResp(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_resumes_after_drop(tmp_path) -> None:
    payload = bytes(range(256)) * 100
    attempts = []

    def opener(req, timeout):
        rng = req.headers.get("Range")
        attempts.append(rng)
        if rng is None:
            # first attempt: deliver half, then the connection drops
            part = tmp_path / "f.bin.part"
            part.write_bytes(payload[:10000])
            raise ConnectionResetError("drop")
        start = int(rng.split("=")[1].rstrip("-"))
        return FakeResp(payload[start:], status=206)

    import autodub.updater.install as mod

    mod.time.sleep = lambda s: None  # no real backoff in tests
    out = download("https://x/f.bin", tmp_path / "f.bin", opener=opener)
    assert out.read_bytes() == payload
    assert attempts == [None, "bytes=10000-"]
