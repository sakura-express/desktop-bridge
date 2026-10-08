"""Exercise actual Windows handles and file locking only in temporary test folders."""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from desktop_bridge.personal import PersonalStore, Profile
from desktop_bridge.state import BridgeError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Actual Windows file handle contracts")


@pytest.fixture
def store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return PersonalStore(workspace)


def test_windows_atomic_context_roundtrip_and_revision_conflict(store):
    assert store.read()["revision"] == 0
    assert not store.directory.exists()
    result = store.update(0, profile=Profile(name="中文名字"))
    assert json.loads((store.directory / "context.json").read_text(encoding="utf-8")) == result
    assert PersonalStore(store.workspace).read() == result
    with pytest.raises(BridgeError, match="Context changed"):
        store.update(0, profile=Profile(name="Stale"))
    assert store.read() == result
    assert not list(store.directory.glob(".context-*.tmp"))


def test_windows_file_lock_serializes_independent_store_instances(store):
    store.update(0, profile=Profile(name="Original"))

    def write(name):
        try:
            PersonalStore(store.workspace).update(1, profile=Profile(name=name))
            return "saved"
        except BridgeError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, ["One", "Two"])) == ["CONTEXT_CONFLICT", "saved"]
    assert store.read()["revision"] == 2


def test_windows_pinned_context_directory_cannot_be_renamed(store):
    store.update(0, profile=Profile(name="Original"))
    with store.locked():
        with pytest.raises(OSError):
            store.directory.rename(store.workspace / "moved")
    assert store.read()["revision"] == 1


def test_windows_replace_failure_preserves_data_and_cleans_temp(store, monkeypatch):
    result = store.update(0, profile=Profile(name="Original"))

    def fail(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(BridgeError):
        store.update(1, profile=Profile(name="Lost"))
    assert store.read() == result
    assert not list(store.directory.glob(".context-*.tmp"))


def test_windows_context_symlinks_are_rejected(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        store.directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(BridgeError):
        store.update(0, profile=Profile(name="Blocked"))
    assert not list(outside.iterdir())


@pytest.mark.parametrize("path", ["C:/Windows/secret.txt", "result.txt:secret", "C:relative.txt"])
def test_windows_artifact_paths_reject_drive_names_and_alternate_streams(path):
    from pydantic import ValidationError

    from desktop_bridge.personal import Task

    with pytest.raises(ValidationError):
        Task(id="test", title="Test", artifacts=[path])
