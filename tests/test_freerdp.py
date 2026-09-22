"""Tests for command-line assembly and binary probing."""

from __future__ import annotations

import pytest

from entrardp import freerdp
from entrardp.config import TOGGLES
from entrardp.freerdp import Connection, Webview, detect_webview


@pytest.fixture
def binary(tmp_path):
    """An executable file standing in for sdl-freerdp."""
    path = tmp_path / "sdl-freerdp"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def all_defaults() -> dict[str, bool]:
    return {key: default for key, _label, _flag, default, _tip in TOGGLES}


# ------------------------------------------------------------------- command

def test_command_always_sets_sec_aad():
    """Without /sec:aad the client falls back to NLA and dies on the KDC."""
    assert "/sec:aad" in Connection(host="vm-1").command()


def test_command_core_arguments():
    cmd = Connection(
        binary="/usr/bin/sdl-freerdp", host="vm-1", username="me@example.com",
        tenant_id="t-1", force_x11=False, manual_res=False,
    ).command()
    assert cmd == [
        "/usr/bin/sdl-freerdp",
        "/v:vm-1",
        "/sec:aad",
        "/azure:tenantid:t-1",
        "/u:me@example.com",
    ]


def test_command_defaults_to_binary_name_when_unset():
    assert Connection(force_x11=False).command()[0] == "sdl-freerdp"


def test_force_x11_wraps_in_env():
    cmd = Connection(binary="/usr/bin/sdl-freerdp", force_x11=True).command()
    assert cmd[:3] == ["env", "SDL_VIDEODRIVER=x11", "/usr/bin/sdl-freerdp"]


def test_disable_compositing_sets_the_webkit_variable():
    cmd = Connection(binary="/usr/bin/sdl-freerdp", force_x11=False,
                     disable_compositing=True).command()
    assert cmd[:3] == [
        "env", "WEBKIT_DISABLE_COMPOSITING_MODE=1", "/usr/bin/sdl-freerdp",
    ]


def test_named_and_custom_environment_combine():
    cmd = Connection(force_x11=True, disable_compositing=True,
                     extra_env="GDK_BACKEND=x11\n\nQT_SCALE_FACTOR=1").command()
    assert cmd[:5] == [
        "env",
        "SDL_VIDEODRIVER=x11",
        "WEBKIT_DISABLE_COMPOSITING_MODE=1",
        "GDK_BACKEND=x11",
        "QT_SCALE_FACTOR=1",
    ]
    assert cmd[5] == "sdl-freerdp"


def test_custom_environment_overrides_a_checkbox():
    """One name, one value: the explicit entry is the one that survives."""
    cmd = Connection(force_x11=True, extra_env="SDL_VIDEODRIVER=wayland").command()
    assert cmd[:2] == ["env", "SDL_VIDEODRIVER=wayland"]
    assert "SDL_VIDEODRIVER=x11" not in cmd


def test_no_env_prefix_when_nothing_is_set():
    cmd = Connection(force_x11=False, extra_env="  \n # comment\n").command()
    assert cmd[0] == "sdl-freerdp"


def test_invalid_environment_lines_are_reported_not_passed(binary):
    conn = Connection(binary=str(binary), host="vm-1", tenant_id="t-1",
                      force_x11=False, extra_env="OOPS\nGDK_BACKEND=x11")
    assert conn.command()[:2] == ["env", "GDK_BACKEND=x11"]
    assert any("OOPS" in p for p in conn.problems())


def test_problems_names_a_shadowed_checkbox(binary):
    conn = Connection(binary=str(binary), host="vm-1", tenant_id="t-1",
                      force_x11=True, extra_env="SDL_VIDEODRIVER=wayland")
    assert any("SDL_VIDEODRIVER" in p for p in conn.problems())


def test_problems_quiet_when_custom_environment_is_valid(binary):
    conn = Connection(binary=str(binary), host="vm-1", tenant_id="t-1",
                      disable_compositing=True, extra_env="GDK_BACKEND=x11")
    assert conn.problems() == []


def test_manual_resolution():
    cmd = Connection(manual_res=True, width=1920, height=1080, force_x11=False).command()
    assert "/w:1920" in cmd and "/h:1080" in cmd

    cmd = Connection(manual_res=False, force_x11=False).command()
    assert not any(a.startswith("/w:") for a in cmd)


def test_toggles_emit_their_flags():
    cmd = Connection(toggles={"cert_ignore": True, "clipboard": False},
                     force_x11=False).command()
    assert "/cert:ignore" in cmd
    assert "/clipboard" not in cmd


def test_home_drive_placeholder_is_expanded():
    cmd = Connection(toggles={"home_drive": True}, force_x11=False).command()
    drive = next(a for a in cmd if a.startswith("/drive:home,"))
    assert "%HOME%" not in drive


def test_pasted_quotes_are_stripped():
    """A quote FreeRDP's parser would treat as fatal must not reach it."""
    cmd = Connection(host='"vm-1"', tenant_id=" t-1 ", force_x11=False).command()
    assert "/v:vm-1" in cmd
    assert "/azure:tenantid:t-1" in cmd


def test_extra_flags_are_split():
    cmd = Connection(extra="/scale:100  /timeout:30000", force_x11=False).command()
    assert "/scale:100" in cmd and "/timeout:30000" in cmd


def test_defaults_produce_a_usable_command():
    """The connection profile verified against a real Entra-joined host."""
    cmd = Connection(host="vm-1", username="me@example.com", tenant_id="t-1",
                     toggles=all_defaults()).command()
    for expected in ("/sec:aad", "/azure:tenantid:t-1", "/cert:ignore", "/f",
                     "/sound:sys:pulse", "/microphone:sys:pulse", "/clipboard"):
        assert expected in cmd


# ------------------------------------------------------------------ problems

def test_problems_reports_each_missing_field():
    issues = " ".join(Connection().problems())
    assert "binary" in issues
    assert "host" in issues
    assert "tenant" in issues


def test_problems_clean_when_configured(binary):
    conn = Connection(binary=str(binary), host="vm-1", tenant_id="t-1")
    assert conn.problems() == []


def test_problems_flags_smart_sizing_with_fullscreen(binary):
    conn = Connection(binary=str(binary), host="vm-1", tenant_id="t-1",
                      toggles={"smart_sizing": True, "fullscreen": True})
    assert any("Smart sizing" in p for p in conn.problems())


def test_problems_does_not_resolve_dns_unless_asked(binary, monkeypatch):
    """problems() is called from the UI thread; resolution blocks."""
    monkeypatch.setattr(
        freerdp, "resolves", lambda *a, **k: pytest.fail("resolves() was called")
    )
    Connection(binary=str(binary), host="vm-1", tenant_id="t-1").problems()


# ------------------------------------------------------------ detect_webview

def test_detect_webview_unknown_for_missing_binary(tmp_path):
    assert detect_webview(None) is Webview.UNKNOWN
    assert detect_webview(tmp_path / "absent") is Webview.UNKNOWN


def test_detect_webview_reads_buildconfig(binary, monkeypatch):
    monkeypatch.setattr(freerdp, "_run", lambda cmd, **k: "WITH_WEBVIEW=ON\n")
    assert detect_webview(binary) is Webview.YES


def test_detect_webview_reports_off(binary, monkeypatch):
    monkeypatch.setattr(freerdp, "_run", lambda cmd, **k: "-DWITH_WEBVIEW=OFF\n")
    assert detect_webview(binary) is Webview.NO


def test_detect_webview_falls_back_to_linkage(binary, monkeypatch):
    def fake_run(cmd, **_kwargs):
        return "libwebkitgtk-6.0.so.4 => /usr/lib64/..." if cmd[0] == "ldd" else ""
    monkeypatch.setattr(freerdp, "_run", fake_run)
    assert detect_webview(binary) is Webview.YES


def test_detect_webview_caches_by_path(binary, monkeypatch):
    calls = []
    monkeypatch.setattr(freerdp, "_run",
                        lambda cmd, **k: calls.append(cmd) or "WITH_WEBVIEW=ON")
    assert detect_webview(binary) is Webview.YES
    before = len(calls)
    assert detect_webview(binary) is Webview.YES
    assert len(calls) == before, "second probe should have been served from cache"


def test_detect_webview_reprobes_a_replaced_binary(binary, monkeypatch):
    """Reinstalling over the same path must not return the old answer."""
    monkeypatch.setattr(freerdp, "_run", lambda cmd, **k: "WITH_WEBVIEW=ON")
    assert detect_webview(binary) is Webview.YES

    binary.write_text("#!/bin/sh\nexit 0\n# rebuilt, different size\n")
    monkeypatch.setattr(freerdp, "_run", lambda cmd, **k: "WITH_WEBVIEW=OFF")
    assert detect_webview(binary) is Webview.NO


@pytest.fixture(autouse=True)
def _clear_webview_cache():
    freerdp._webview_cache.clear()
    yield
    freerdp._webview_cache.clear()
