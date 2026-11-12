"""Tests for command-line assembly and binary probing."""

from __future__ import annotations

import pytest

from entrardp import freerdp
from entrardp.config import TOGGLES
from entrardp.freerdp import Capability, Connection, SignIn, detect_signin, find_binary


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


def test_problems_silent_on_smart_sizing_with_fullscreen(binary):
    """The Wayland artifact bug this once warned about is fixed upstream.

    Asserted rather than simply deleted, so reintroducing the warning
    without revisiting the fix fails here instead of in someone's session.
    """
    conn = Connection(binary=str(binary), host="vm-1", tenant_id="t-1",
                      toggles={"smart_sizing": True, "fullscreen": True})
    assert conn.problems() == []


def test_problems_does_not_resolve_dns_unless_asked(binary, monkeypatch):
    """problems() is called from the UI thread; resolution blocks."""
    monkeypatch.setattr(
        freerdp, "resolves", lambda *a, **k: pytest.fail("resolves() was called")
    )
    Connection(binary=str(binary), host="vm-1", tenant_id="t-1").problems()


# ------------------------------------------------------------ auth helper

def test_auth_helper_joins_the_azure_argument():
    """FreeRDP reads /azure: as one comma-separated list of sub-options."""
    cmd = Connection(tenant_id="t-1", auth_helper="/usr/libexec/freerdp-xdg-aad-helper",
                     force_x11=False).command()
    assert "/azure:tenantid:t-1,auth-helper:/usr/libexec/freerdp-xdg-aad-helper" in cmd
    assert sum(a.startswith("/azure:") for a in cmd) == 1


def test_no_auth_helper_leaves_autodetection_to_freerdp():
    cmd = Connection(tenant_id="t-1", force_x11=False).command()
    assert "/azure:tenantid:t-1" in cmd
    assert not any("auth-helper" in a for a in cmd)


# ------------------------------------------------------------------ floatbar

def executable(path):
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_floatbar_runs_the_x11_client_beside_the_sdl_one(tmp_path):
    """SDL ignores /floatbar; only the X11 client draws the toolbar."""
    sdl = executable(tmp_path / "sdl-freerdp")
    x11 = executable(tmp_path / "xfreerdp")
    cmd = Connection(binary=str(sdl), host="vm-1", floatbar=True, force_x11=False).command()
    assert cmd[0] == str(x11)
    assert freerdp.FLOATBAR_FLAG in cmd
    assert "/sec:aad" in cmd


def test_floatbar_without_an_x11_client_falls_back_and_says_so(tmp_path):
    sdl = executable(tmp_path / "sdl-freerdp")
    conn = Connection(binary=str(sdl), host="vm-1", tenant_id="t-1",
                      floatbar=True, force_x11=False)
    cmd = conn.command()
    assert cmd[0] == str(sdl)
    assert not any(a.startswith("/floatbar") for a in cmd)
    assert any("xfreerdp" in issue for issue in conn.problems())


def test_floatbar_off_keeps_the_sdl_client(tmp_path):
    sdl = executable(tmp_path / "sdl-freerdp")
    executable(tmp_path / "xfreerdp")
    cmd = Connection(binary=str(sdl), force_x11=False).command()
    assert cmd[0] == str(sdl)
    assert not any(a.startswith("/floatbar") for a in cmd)


def test_versioned_sdl_client_prefers_the_versioned_x11_client(tmp_path):
    """Upstream's nightly spec installs sdl-freerdp3 and xfreerdp3."""
    sdl = executable(tmp_path / "sdl-freerdp3")
    executable(tmp_path / "xfreerdp")
    x11 = executable(tmp_path / "xfreerdp3")
    assert freerdp.find_x11_client(sdl) == str(x11)


# ------------------------------------------------------------- detect_signin

def fake_tools(ldd="", buildconfig=""):
    """A stand-in for _run answering ldd and /buildconfig."""
    def run(cmd, **_kwargs):
        return ldd if cmd[0] == "ldd" else buildconfig
    return run


@pytest.fixture
def prefix(tmp_path):
    """An install prefix: bin/sdl-freerdp with an empty libexec beside it."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "libexec").mkdir()
    binary = tmp_path / "bin" / "sdl-freerdp"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return tmp_path


def install_helper(prefix, filename):
    path = prefix / "libexec" / filename
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_parse_buildconfig_forms():
    flags = freerdp.parse_buildconfig(
        "WITH_AAD=ON -DWITH_WEBVIEW=OFF -DWITH_XDG_AAD_AUTH_HELPER:BOOL=on CMAKE_X=1"
    )
    assert flags == {"WITH_AAD": "ON", "WITH_WEBVIEW": "OFF",
                     "WITH_XDG_AAD_AUTH_HELPER": "ON"}


def test_detect_signin_unknown_for_missing_binary(tmp_path):
    assert detect_signin(None).sign_in is SignIn.UNKNOWN
    assert detect_signin(tmp_path / "absent").sign_in is SignIn.UNKNOWN


def test_missing_libraries_mean_broken(prefix, monkeypatch):
    """What an OS upgrade does to a private build: the loader cannot start it."""
    ldd = ("\tlibssl.so.3 => not found\n"
           "\tlibSDL3.so.0 => /usr/lib64/libSDL3.so.0 (0x7f)\n"
           "\tlibcrypto.so.3 => not found\n"
           "\tlibssl.so.3 => not found\n")
    monkeypatch.setattr(freerdp, "_run", fake_tools(ldd=ldd, buildconfig="WITH_WEBVIEW=ON"))
    cap = detect_signin(prefix / "bin" / "sdl-freerdp")
    assert cap.sign_in is SignIn.BROKEN
    assert cap.missing_libs == ("libssl.so.3", "libcrypto.so.3")
    assert not cap.can_sign_in


def test_installed_helper_is_found_in_libexec(prefix, monkeypatch):
    """Fedora 45's package: the XDG helper, and WITH_WEBVIEW gone entirely."""
    helper = install_helper(prefix, "freerdp-xdg-aad-helper")
    monkeypatch.setattr(freerdp, "_run", fake_tools(
        buildconfig="WITH_AAD=ON WITH_WEBVIEW_AAD_AUTH_HELPER=OFF WITH_XDG_AAD_AUTH_HELPER=ON"))
    cap = detect_signin(prefix / "bin" / "sdl-freerdp")
    assert cap.sign_in is SignIn.HELPER
    assert cap.helpers == {"xdg": str(helper)}
    assert cap.can_sign_in


def test_helpers_are_listed_in_autodetect_order(prefix, monkeypatch):
    install_helper(prefix, "freerdp-webview-aad-helper")
    install_helper(prefix, "freerdp-xdg-aad-helper")
    monkeypatch.setattr(freerdp, "_run", fake_tools(
        buildconfig="WITH_WEBVIEW_AAD_AUTH_HELPER=ON WITH_XDG_AAD_AUTH_HELPER=ON"))
    assert list(detect_signin(prefix / "bin" / "sdl-freerdp").helpers) == ["xdg", "webview"]


def test_helper_flag_without_the_program_is_manual(prefix, monkeypatch):
    monkeypatch.setattr(freerdp, "_run", fake_tools(buildconfig="WITH_XDG_AAD_AUTH_HELPER=ON"))
    cap = detect_signin(prefix / "bin" / "sdl-freerdp")
    assert cap.sign_in is SignIn.MANUAL
    assert cap.missing_helpers == ("xdg",)


def test_installed_helper_without_its_flag_is_ignored(prefix, monkeypatch):
    """A leftover file from another build is not this build's helper."""
    install_helper(prefix, "freerdp-xdg-aad-helper")
    monkeypatch.setattr(freerdp, "_run", fake_tools(buildconfig="WITH_AAD=ON"))
    assert detect_signin(prefix / "bin" / "sdl-freerdp").sign_in is SignIn.MANUAL


def test_legacy_webview_build_is_embedded(binary, monkeypatch):
    monkeypatch.setattr(freerdp, "_run", fake_tools(buildconfig="-DWITH_WEBVIEW=ON"))
    assert detect_signin(binary).sign_in is SignIn.EMBEDDED


def test_new_helper_option_is_not_mistaken_for_legacy_webview(binary, monkeypatch):
    """WITH_WEBVIEW_AAD_AUTH_HELPER contains WITH_WEBVIEW; it must not match it."""
    monkeypatch.setattr(freerdp, "_run", fake_tools(
        buildconfig="WITH_WEBVIEW_AAD_AUTH_HELPER=OFF"))
    assert detect_signin(binary).sign_in is SignIn.MANUAL


def test_linkage_is_the_last_resort(binary, monkeypatch):
    monkeypatch.setattr(freerdp, "_run", fake_tools(
        ldd="libwebkitgtk-6.0.so.4 => /usr/lib64/libwebkitgtk-6.0.so.4"))
    assert detect_signin(binary).sign_in is SignIn.EMBEDDED


def test_nothing_learned_is_unknown(binary, monkeypatch):
    monkeypatch.setattr(freerdp, "_run", fake_tools())
    assert detect_signin(binary).sign_in is SignIn.UNKNOWN


def test_detect_signin_caches_by_path(binary, monkeypatch):
    calls = []
    monkeypatch.setattr(freerdp, "_run",
                        lambda cmd, **k: calls.append(cmd) or "WITH_WEBVIEW=ON")
    assert detect_signin(binary).sign_in is SignIn.EMBEDDED
    before = len(calls)
    assert detect_signin(binary).sign_in is SignIn.EMBEDDED
    assert len(calls) == before, "second probe should have been served from cache"


def test_detect_signin_reprobes_a_replaced_binary(binary, monkeypatch):
    """Reinstalling over the same path must not return the old answer."""
    monkeypatch.setattr(freerdp, "_run", fake_tools(buildconfig="WITH_WEBVIEW=ON"))
    assert detect_signin(binary).sign_in is SignIn.EMBEDDED

    binary.write_text("#!/bin/sh\nexit 0\n# rebuilt, different size\n")
    monkeypatch.setattr(freerdp, "_run", fake_tools(buildconfig="WITH_WEBVIEW=OFF"))
    assert detect_signin(binary).sign_in is SignIn.MANUAL


# --------------------------------------------------------------- find_binary

def test_find_binary_skips_a_broken_build(monkeypatch):
    """The Fedora 45 case: a private build broken by the upgrade ranks first."""
    monkeypatch.setattr(freerdp, "candidates", lambda: ["/private", "/usr/bin/x"])
    caps = {"/private": Capability(SignIn.BROKEN, missing_libs=("libssl.so.3",)),
            "/usr/bin/x": Capability(SignIn.HELPER, helpers={"xdg": "/h"})}
    monkeypatch.setattr(freerdp, "detect_signin", caps.__getitem__)
    assert find_binary() == "/usr/bin/x"


def test_find_binary_prefers_one_that_starts_over_one_that_cannot(monkeypatch):
    monkeypatch.setattr(freerdp, "candidates", lambda: ["/a", "/b"])
    caps = {"/a": Capability(SignIn.BROKEN), "/b": Capability(SignIn.MANUAL)}
    monkeypatch.setattr(freerdp, "detect_signin", caps.__getitem__)
    assert find_binary() == "/b"


def test_find_binary_none_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(freerdp, "candidates", list)
    assert find_binary() is None


def test_cli_exit_status_reflects_sign_in(monkeypatch, capsys):
    caps = {"/ok": Capability(SignIn.HELPER, helpers={"xdg": "/h"}),
            "/bad": Capability(SignIn.BROKEN, missing_libs=("libssl.so.3",))}
    monkeypatch.setattr(freerdp, "detect_signin", caps.__getitem__)
    assert freerdp.main(["/bad"]) == 1
    assert freerdp.main(["/bad", "/ok"]) == 0
    assert "libssl.so.3" in capsys.readouterr().out


@pytest.fixture(autouse=True)
def _clear_signin_cache():
    freerdp._cache.clear()
    yield
    freerdp._cache.clear()
