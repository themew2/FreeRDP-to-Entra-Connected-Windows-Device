"""Locating a usable FreeRDP binary and building its command line.

The central problem this module solves: two `sdl-freerdp` binaries with the
same name can sign in to Entra in completely different ways, or not at all.
Name equality is not build equality, so the binary must be probed rather than
assumed.

FreeRDP has had two generations of browser sign-in:

  3.16 – 3.31  WITH_WEBVIEW, an embedded WebKitGTK window inside the client.
  3.32 +       Out-of-process sign-in helpers installed to libexec and chosen
               with /azure:auth-helper. WITH_WEBVIEW no longer exists.

A build with neither falls back to printing a URL that has to be pasted into a
browser and the result pasted back. A build of either generation can also be
unable to start at all, when the shared libraries it was linked against have
been replaced by an OS upgrade. All of that is decided here.

Run as `python3 -m entrardp.freerdp [binary...]` to print the same verdict the
GUI shows. install.sh uses this to decide whether compiling is needed at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .config import TOGGLES, clean_value, expand_flag, parse_env

# Naming varies with WITH_CLIENT_SDL_VERSIONED: distribution packages commonly
# build it OFF (sdl-freerdp), while FreeRDP's own nightly spec sets it ON
# (sdl-freerdp3). Both are the same client.
BIN_NAMES = ["sdl-freerdp3", "sdl-freerdp", "sdl3-freerdp"]

# Searched in order. All are *installed* prefixes.
#
# Binaries are deliberately never taken from a CMake build directory. Upstream
# guidance is to run `cmake --build <dir> --target install` and use the result;
# build-tree binaries pick up wrong library paths and resource locations.
PREFERRED_PATHS = [
    # The prefix produced by scripts/build-freerdp.sh, built from the newest
    # release tag with every sign-in helper enabled. It ranks first because a
    # build made for this app is the most likely to offer every sign-in
    # method; a distribution package typically ships just one.
    Path.home() / ".local/share/entrardp/freerdp/bin/sdl-freerdp",
    Path("/app/bin/sdl-freerdp"),          # sandboxed prefix, if present
    Path("/usr/local/bin/sdl-freerdp"),
    # The prefix used by scripts/build-freerdp-rpm.sh, from the same release
    # tag but packaged with upstream's nightly spec. It ranks below the
    # private prefix because the spec hardcodes version 3.0-0, so rpm cannot
    # tell one rebuild from another.
    Path("/opt/freerdp-nightly/bin/sdl-freerdp3"),
    Path("/opt/freerdp-nightly/bin/sdl-freerdp"),
    # Distribution packages are found through PATH after these.
    #
    # Ranking is only a tie-breaker. find_binary skips any build that cannot
    # start and prefers any that can sign in, wherever either sits here, so a
    # private build broken by an OS upgrade gives way to a working package.
]

# Out-of-process sign-in helpers, FreeRDP 3.32 and later: short name, the cmake
# option that builds it, its installed file name, and how it presents to the
# user. Listed in upstream's autodetect order (client/common/aad_helper.c), so
# "automatic" in the GUI means the first available entry here.
HELPERS: list[tuple[str, str, str, str]] = [
    ("xdg", "WITH_XDG_AAD_AUTH_HELPER", "freerdp-xdg-aad-helper",
     "Default web browser"),
    ("qt", "WITH_QT_AAD_AUTH_HELPER", "freerdp-qt-aad-helper",
     "Embedded window (Qt WebEngine)"),
    ("webview", "WITH_WEBVIEW_AAD_AUTH_HELPER", "freerdp-webview-aad-helper",
     "Embedded window (WebKitGTK)"),
]
HELPER_LABELS = {name: label for name, _flag, _file, label in HELPERS}

# FreeRDP's X11 client, which the on-screen toolbar requires.
#
# The SDL client parses /floatbar, because option parsing is shared, and then
# ignores it: only the X11 and Windows clients implement a floatbar. Both
# clients come from the same build, and the X11 one obtains Entra tokens
# through the same sign-in helpers, so switching between them changes the
# window, not the sign-in. Named xfreerdp3 when built with
# WITH_BINARY_VERSIONING, as upstream's nightly spec does.
X11_NAMES = ("xfreerdp", "xfreerdp3")

# Shown in fullscreen only, which is when the window offers no controls of its
# own. Not sticky: it starts visible, slides away, and returns when the mouse
# reaches the top edge of the screen.
FLOATBAR_FLAG = "/floatbar:sticky:off,default:visible,show:fullscreen"


def find_x11_client(sdl_binary: str | Path | None) -> str | None:
    """The X11 client installed beside an SDL client, if there is one.

    Looked for in the SDL client's own directory, symlinks resolved, so it
    always comes from the same FreeRDP build and shares its sign-in helpers.
    A versioned SDL name (sdl-freerdp3) prefers the versioned X11 name.
    """
    if not sdl_binary or not is_usable(sdl_binary):
        return None
    sdl = Path(sdl_binary).resolve()
    names = reversed(X11_NAMES) if sdl.name.endswith("3") else X11_NAMES
    for name in names:
        path = sdl.parent / name
        if is_usable(path):
            return str(path)
    return None

# An AVD workspace file embeds a certificate that expires a few months
# after it is downloaded. There is no public API to refresh one, so the
# only remedy is a manual re-download from the AVD web client.
WORKSPACE_STALE_DAYS = 90

# The environment variables with a checkbox of their own.
#
# The AAD webview popup does not map reliably on native Wayland, so the client
# is run under XWayland.
X11_ENV = ("SDL_VIDEODRIVER", "x11")
# WebKitGTK's accelerated compositing crashes the Entra sign-in webview on some
# GPU and driver combinations. Disabling it costs a little rendering
# performance and gets a sign-in window that stays up.
COMPOSITING_ENV = ("WEBKIT_DISABLE_COMPOSITING_MODE", "1")


class SignIn(Enum):
    """How a binary will present Entra sign-in."""

    HELPER = "helper"       # 3.32+: at least one helper installed beside it
    EMBEDDED = "embedded"   # 3.16–3.31: WITH_WEBVIEW=ON, in-process window
    MANUAL = "manual"       # neither: print a URL, paste the result back
    BROKEN = "broken"       # cannot start: linked libraries are missing
    UNKNOWN = "unknown"     # not probed, or the probe learned nothing


@dataclass(frozen=True)
class Capability:
    sign_in: SignIn
    # Helper short name -> installed path, in autodetect order.
    helpers: dict[str, str] = field(default_factory=dict)
    # Helpers the build was configured with but whose program is missing.
    missing_helpers: tuple[str, ...] = ()
    # Shared libraries the dynamic loader cannot find.
    missing_libs: tuple[str, ...] = ()

    @property
    def can_sign_in(self) -> bool:
        return self.sign_in in (SignIn.HELPER, SignIn.EMBEDDED)


def candidates() -> list[str]:
    """Every installed FreeRDP SDL client, in PREFERRED_PATHS then PATH order."""
    found: list[str] = []
    for path in PREFERRED_PATHS:
        if is_usable(path):
            found.append(str(path))
    for name in BIN_NAMES:
        hit = shutil.which(name)
        if hit and hit not in found:
            found.append(hit)
    return found


def find_binary() -> str | None:
    """Return the best available FreeRDP SDL client.

    First choice is the first candidate that can sign in. Failing that, the
    first that at least starts, so the GUI can explain what it lacks. A build
    that cannot start is returned only when there is nothing else.
    """
    found = candidates()
    if not found:
        return None
    probed = [(c, detect_signin(c)) for c in found]
    for candidate, cap in probed:
        if cap.can_sign_in:
            return candidate
    for candidate, cap in probed:
        if cap.sign_in is not SignIn.BROKEN:
            return candidate
    return found[0]


def is_usable(path: str | Path) -> bool:
    p = Path(path)
    return p.is_file() and os.access(p, os.X_OK)


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


# Results keyed by path, modification time and size.
#
# Each miss costs two subprocesses. find_binary probes every candidate and the
# GUI probes whatever the user selects, so the same binary is asked repeatedly
# within one session. Including mtime and size in the key means reinstalling
# FreeRDP over the same path re-probes rather than returning the previous
# build's answer.
_cache: dict[tuple[str, int, int], Capability] = {}


def detect_signin(binary: str | Path | None) -> Capability:
    """Determine whether, and how, a binary can sign in to Entra.

    Blocks for as long as `ldd` and `/buildconfig` take. Never call it from a
    UI thread on a cold cache; see gui.CapabilityProbe.
    """
    if not binary or not is_usable(binary):
        return Capability(SignIn.UNKNOWN)
    try:
        stat = Path(binary).stat()
    except OSError:
        return Capability(SignIn.UNKNOWN)

    key = (str(binary), stat.st_mtime_ns, stat.st_size)
    if key not in _cache:
        _cache[key] = _probe(Path(binary))
    return _cache[key]


def parse_buildconfig(text: str) -> dict[str, str]:
    """Pull WITH_* settings out of `/buildconfig` output.

    Accepts the forms seen across versions and packagers: `WITH_X=ON`,
    `-DWITH_X=ON` and `-DWITH_X:BOOL=ON`. Names and values are uppercased.
    """
    flags: dict[str, str] = {}
    for token in text.split():
        name, sep, value = token.partition("=")
        if not sep:
            continue
        name = name.removeprefix("-D").partition(":")[0].upper()
        if name.startswith("WITH_"):
            flags[name] = value.strip().upper()
    return flags


def missing_libraries(ldd_output: str) -> tuple[str, ...]:
    """Library names `ldd` reports as `=> not found`, first occurrence only."""
    missing: list[str] = []
    for line in ldd_output.splitlines():
        if "=> not found" in line:
            name = line.split("=>")[0].strip()
            if name and name not in missing:
                missing.append(name)
    return tuple(missing)


def helper_dirs(binary: Path) -> list[Path]:
    """Where FreeRDP looks for its sign-in helpers, relative to the client.

    Mirrors aad_auth_helper_binary_dirs upstream: the client's own directory
    and ../libexec beside it. The install prefix compiled into the client is
    also searched upstream, but for every layout this app knows about
    (/usr, /opt/freerdp-nightly, the private prefix) that is the same place.
    """
    real = binary.resolve()
    return [real.parent, real.parent.parent / "libexec"]


def _probe(binary: Path) -> Capability:
    linked = _run(["ldd", str(binary)])
    missing = missing_libraries(linked)
    if missing:
        # Checked first: a binary that cannot load cannot report its own
        # build configuration either, and "rebuild it" is the only advice
        # that applies.
        return Capability(SignIn.BROKEN, missing_libs=missing)

    flags = parse_buildconfig(_run([str(binary), "/buildconfig"]))

    helpers: dict[str, str] = {}
    configured_but_absent: list[str] = []
    dirs = helper_dirs(binary)
    for name, flag, filename, _label in HELPERS:
        if flags.get(flag) != "ON":
            continue
        path = next((d / filename for d in dirs if is_usable(d / filename)), None)
        if path:
            helpers[name] = str(path)
        else:
            configured_but_absent.append(name)

    if helpers:
        return Capability(SignIn.HELPER, helpers=helpers,
                          missing_helpers=tuple(configured_but_absent))
    if flags.get("WITH_WEBVIEW") == "ON":
        return Capability(SignIn.EMBEDDED)
    if flags:
        # It answered /buildconfig and offers no sign-in window either way.
        return Capability(SignIn.MANUAL,
                          missing_helpers=tuple(configured_but_absent))
    # No usable /buildconfig. Linkage is the remaining clue to the older,
    # in-process webview; without it there is nothing to go on.
    if "webkit" in linked.lower():
        return Capability(SignIn.EMBEDDED)
    return Capability(SignIn.UNKNOWN)


def resolves(host: str, timeout: float = 3.0) -> bool:
    """Check DNS or /etc/hosts resolution, mirroring the wrapper script.

    The remote hostname must match the Entra-registered device name exactly,
    so a resolution failure is worth surfacing before attempting a connection.

    This blocks for as long as the resolver takes, which for a nonexistent name
    can be seconds. Never call it from a UI thread; see gui.DnsProbe.
    """
    if not host:
        return False
    try:
        return subprocess.run(
            ["getent", "hosts", host],
            capture_output=True, check=False, timeout=timeout,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass
class Connection:
    """Everything needed to assemble one FreeRDP invocation."""

    binary: str = ""
    host: str = ""
    username: str = ""
    tenant_id: str = ""
    toggles: dict[str, bool] = field(default_factory=dict)
    manual_res: bool = True
    width: int = 2560
    height: int = 1440
    force_x11: bool = True
    disable_compositing: bool = False
    # Free-form KEY=VALUE lines, one per line. Applied after the two named
    # variables above, so an entry here can deliberately override either.
    extra_env: str = ""
    extra: str = ""
    # Path to a sign-in helper program, passed as /azure:auth-helper. Empty
    # leaves the choice to FreeRDP's own autodetection.
    auth_helper: str = ""
    # Run the X11 client from the same install with an on-screen toolbar
    # (minimize, restore, close) in fullscreen. See FLOATBAR_FLAG.
    floatbar: bool = False
    # Path to an Azure Virtual Desktop workspace file (.rdpw). FreeRDP 3
    # parses these natively, so no parsing happens here. The file carries
    # the host, the gateway and a load balancing token, which is why /v:
    # is omitted whenever one is set.
    workspace_file: str = ""

    def env_assignments(self) -> list[str]:
        """The KEY=VALUE arguments for `env`, in the order they take effect.

        A name is emitted once. `env` applies assignments left to right, so a
        duplicate would work anyway, but the command preview is the thing
        people read to understand what will run and a name appearing twice
        with two values reads as a bug. Collapsing through a dict keeps each
        name where it first appeared while the last value wins, which is what
        `env` would have done.
        """
        env: dict[str, str] = {}
        if self.force_x11:
            env[X11_ENV[0]] = X11_ENV[1]
        if self.disable_compositing:
            env[COMPOSITING_ENV[0]] = COMPOSITING_ENV[1]
        for name, value in parse_env(self.extra_env)[0]:
            env[name] = value
        return [f"{name}={value}" for name, value in env.items()]

    def command(self) -> list[str]:
        binary = clean_value(self.binary) or "sdl-freerdp"
        floatbar = False
        if self.floatbar:
            # Without an X11 client beside it, the SDL client runs as usual
            # and problems() says why there is no toolbar.
            x11 = find_x11_client(binary)
            if x11:
                binary, floatbar = x11, True
        args: list[str] = []

        env = self.env_assignments()
        if env:
            args += ["env", *env]
        args.append(binary)

        # An AVD workspace file supplies the target itself, so it replaces
        # /v: rather than supplementing it. Passing both is ambiguous and
        # FreeRDP does not define which wins.
        workspace = clean_value(self.workspace_file)
        if workspace:
            # Positional, as FreeRDP expects for .rdp and .rdpw input.
            args.append(workspace)
            # AVD routes through the ARM gateway. Without this the client
            # attempts a direct connection to the session host and fails.
            args.append("/gateway:type:arm")
        else:
            host = clean_value(self.host)
            if host:
                args.append(f"/v:{host}")

        # /sec:aad is mandatory alongside /azure. Without it the client falls
        # back to NLA/Kerberos and dies with "Cannot find KDC for realm".
        args.append("/sec:aad")

        # One /azure: argument carrying comma-separated sub-options, which is
        # how FreeRDP's parse_aad_options reads it.
        azure = []
        tenant = clean_value(self.tenant_id)
        if tenant:
            azure.append(f"tenantid:{tenant}")
        helper = clean_value(self.auth_helper)
        if helper:
            azure.append(f"auth-helper:{helper}")
        if azure:
            args.append("/azure:" + ",".join(azure))

        user = clean_value(self.username)
        if user:
            args.append(f"/u:{user}")

        for key, _label, flag, _default, _tip in TOGGLES:
            if self.toggles.get(key):
                args.append(expand_flag(flag))

        if floatbar:
            args.append(FLOATBAR_FLAG)

        if self.manual_res:
            args += [f"/w:{self.width}", f"/h:{self.height}"]

        extra = clean_value(self.extra)
        if extra:
            args += extra.split()

        return args

    def problems(self, check_dns: bool = False) -> list[str]:
        """Non-fatal warnings worth showing before launching.

        DNS resolution is opt-in because it blocks. Callers on a UI thread
        should leave check_dns False and probe separately in the background.
        """
        issues = []
        if not is_usable(clean_value(self.binary)):
            issues.append("FreeRDP binary not found or not executable.")
        workspace = clean_value(self.workspace_file)
        if workspace:
            wf = Path(workspace).expanduser()
            if not wf.is_file():
                issues.append(f"Workspace file not found: {workspace}")
            else:
                # The certificate inside an .rdpw expires a few months
                # after download; connecting past that fails with 0x1608.
                # mtime is a proxy for download time, not the real expiry,
                # so this warns rather than blocks.
                age = (time.time() - wf.stat().st_mtime) / 86400
                if age > WORKSPACE_STALE_DAYS:
                    issues.append(
                        f"Workspace file is {int(age)} days old. These expire; "
                        "re-download it from the AVD web client if sign-in fails."
                    )
            if clean_value(self.host):
                issues.append(
                    "Both a host name and a workspace file are set; "
                    "the workspace file takes precedence."
                )
        elif not clean_value(self.host):
            issues.append("No host name set.")
        elif check_dns and not resolves(clean_value(self.host)):
            issues.append(
                f"'{clean_value(self.host)}' does not resolve via DNS or /etc/hosts. "
                "It must match the Entra-registered device name exactly."
            )
        if not clean_value(self.tenant_id):
            issues.append("No tenant ID set; Entra sign-in will likely fail.")
        if self.floatbar and is_usable(clean_value(self.binary)) \
                and not find_x11_client(clean_value(self.binary)):
            issues.append(
                "On-screen toolbar needs xfreerdp beside the selected client; "
                "none found, so the session starts without it."
            )
        custom, env_errors = parse_env(self.extra_env)
        issues += [f"Custom environment: {e}" for e in env_errors]
        # A checkbox that stays ticked while a custom line replaces its value
        # is the kind of thing that costs an hour. Name it.
        named = [X11_ENV[0]] if self.force_x11 else []
        if self.disable_compositing:
            named.append(COMPOSITING_ENV[0])
        shadowed = [n for n in named if n in {name for name, _value in custom}]
        if shadowed:
            issues.append(
                f"Custom environment overrides {', '.join(shadowed)}; "
                "the checkbox value is not used."
            )
        return issues


# ------------------------------------------------------------------- report

def describe(cap: Capability) -> str:
    """One line on what a capability means for the person signing in."""
    if cap.sign_in is SignIn.HELPER:
        names = ", ".join(HELPER_LABELS[n].lower() for n in cap.helpers)
        return f"sign-in helper available: {names}"
    if cap.sign_in is SignIn.EMBEDDED:
        return "embedded sign-in window (WITH_WEBVIEW, FreeRDP 3.16-3.31)"
    if cap.sign_in is SignIn.MANUAL:
        return "no sign-in window: prints a URL to copy and paste back"
    if cap.sign_in is SignIn.BROKEN:
        return f"cannot start, missing libraries: {', '.join(cap.missing_libs)}"
    return "sign-in support could not be determined"


def main(argv: list[str]) -> int:
    """Print the verdict for each binary given, or every installed candidate.

    Exit status 0 when at least one of them can sign in, which is what
    install.sh asks before deciding to compile.
    """
    binaries = argv or candidates()
    if not binaries:
        print("no FreeRDP SDL client found")
        return 1
    ok = False
    for binary in binaries:
        cap = detect_signin(binary)
        ok = ok or cap.can_sign_in
        print(f"{cap.sign_in.value:8} {binary}: {describe(cap)}")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
