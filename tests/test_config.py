"""Tests for flag definitions and profile persistence."""

from __future__ import annotations

import json
import stat

import pytest

from entrardp import config
from entrardp.config import (
    MUTUALLY_EXCLUSIVE,
    TOGGLES,
    ProfileStore,
    clean_value,
    expand_flag,
)

# ---------------------------------------------------------------- clean_value

@pytest.mark.parametrize(("raw", "expected"), [
    (None, ""),
    ("", ""),
    ("  host  ", "host"),
    ('"host"', "host"),
    ("'host'", "host"),
    ('""host""', "host"),
    # Non-breaking spaces come in from portals and rendered documentation.
    (" host ", "host"),
    # Unbalanced quotes are what FreeRDP's parser treats as fatal.
    ('"host', "host"),
    ("ho\"st", "host"),
])
def test_clean_value(raw, expected):
    assert clean_value(raw) == expected


def test_clean_value_keeps_inner_structure():
    """Flag syntax must survive: only quotes and edge whitespace are stripped."""
    assert clean_value("  /drive:home,/home/me  ") == "/drive:home,/home/me"


# --------------------------------------------------------------- flag tables

def test_toggle_keys_unique():
    keys = [key for key, *_ in TOGGLES]
    assert len(keys) == len(set(keys))


def test_mutually_exclusive_keys_exist():
    """A pair naming a key not in TOGGLES would raise a KeyError in the GUI."""
    keys = {key for key, *_ in TOGGLES}
    for a, b in MUTUALLY_EXCLUSIVE:
        assert a in keys, a
        assert b in keys, b


def test_no_exclusive_pair_defaults_to_both_on():
    """Defaults must not themselves produce a combination FreeRDP refuses."""
    defaults = {key: default for key, _label, _flag, default, _tip in TOGGLES}
    for a, b in MUTUALLY_EXCLUSIVE:
        assert not (defaults[a] and defaults[b]), f"{a} and {b} both default on"


def test_expand_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("USER", "someone")
    assert expand_flag("/drive:home,%HOME%") == f"/drive:home,{tmp_path}"
    assert expand_flag("/u:%USER%") == "/u:someone"
    assert expand_flag("/cert:ignore") == "/cert:ignore"


# -------------------------------------------------------------- ProfileStore

def test_profile_roundtrip(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.put("work", {"host": "vm-1", "tenant": "t-1"})

    assert ProfileStore(path).profiles == {"work": {"host": "vm-1", "tenant": "t-1"}}
    assert store.names() == ["work"]

    store.delete("work")
    assert ProfileStore(path).profiles == {}


def test_profile_file_is_owner_only(tmp_path):
    """Profiles hold tenant IDs and internal host names."""
    path = tmp_path / "profiles.json"
    ProfileStore(path).put("work", {"host": "vm-1"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "profiles.json"
    ProfileStore(path).put("work", {"host": "vm-1"})
    assert [p.name for p in tmp_path.iterdir()] == ["profiles.json"]


def test_corrupt_profile_file_does_not_raise(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{ not json")
    assert ProfileStore(path).profiles == {}


def test_non_dict_profile_file_is_ignored(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(["a", "list"]))
    assert ProfileStore(path).profiles == {}


def test_last_used_ignores_deleted_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    store = ProfileStore(tmp_path / "profiles.json")
    store.put("work", {"host": "vm-1"})
    store.last_used = "work"
    assert store.last_used == "work"

    store.delete("work")
    assert store.last_used is None


def test_state_file_is_owner_only(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(config, "STATE_FILE", state)
    store = ProfileStore(tmp_path / "profiles.json")
    store.put("work", {"host": "vm-1"})
    store.last_used = "work"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


def test_last_used_survives_unwritable_state(tmp_path, monkeypatch):
    """Recording the last profile is a convenience, never a hard failure."""
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "nope" / "state.json")
    monkeypatch.setattr(config.Path, "mkdir", _raise_oserror)
    store = ProfileStore(tmp_path / "profiles.json")
    store.last_used = "work"  # must not raise


def _raise_oserror(*_args, **_kwargs):
    raise OSError("read-only")
