"""Locating a usable FreeRDP binary and building its command line.

The central problem this module solves: `sdl-freerdp` from a distro package and
`sdl-freerdp` built from source are the same program name with different
capabilities. Distro builds default to WITH_WEBVIEW=OFF, which silently
downgrades Entra authentication from an embedded browser popup to a
copy-a-URL-into-your-browser flow. Name equality is not build equality, so the
binary must be probed rather than assumed.
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
    # release tag. This ranks first because it is the build this project
    # targets and the one its own tooling produces.
    Path.home() / ".local/share/entrardp/freerdp/bin/sdl-freerdp",
    Path("/app/bin/sdl-freerdp"),          # sandboxed prefix, if present
    Path("/usr/local/bin/sdl-freerdp"),
    # The prefix used by scripts/build-freerdp-rpm.sh, from the same release
    # tag but packaged with upstream's nightly spec. That script overrides the
    # spec's nightly-testing defaults (Debug, AddressSanitizer,
    # WITH_VERBOSE_WINPR_ASSERT, experimental VAAPI encoding) unless
    # RELEASE_BUILD=0, so the audio regression those once caused no longer
    # applies to a default build.
    #
    # It still ranks last, for a reason the script cannot fix: the spec
    # hardcodes version 3.0-0, so rpm cannot tell one rebuild from another and
    # a stale binary can persist unnoticed after an apparently successful
    # upgrade. Ranking is only a tie-breaker in any case — find_binary
    # promotes any webview-capable build over the order below.
    Path("/opt/freerdp-nightly/bin/sdl-freerdp3"),
    Path("/opt/freerdp-nightly/bin/sdl-freerdp"),
]

# Webview support landed upstream in this release; older builds cannot have it
# regardless of how they were configured.
WEBVIEW_MIN_VERSION = (3, 16, 0)

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


class Webview(Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def find_binary() -> str | None:
    """Return the best available FreeRDP SDL client, preferring webview builds."""
    candidates: list[str] = []
    for path in PREFERRED_PATHS:
        if is_usable(path):
            candidates.append(str(path))
    for name in BIN_NAMES:
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    if not candidates:
        return None
    # A webview-capable build always wins, wherever it sits in the order.
    for candidate in candidates:
        if detect_webview(candidate) is Webview.YES:
            return candidate
    return candidates[0]


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
# Each miss costs up to three subprocesses, the last of which is `nm` over a
# binary large enough to take seconds. find_binary probes every candidate and
# the GUI probes whatever the user selects, so the same binary is asked
# repeatedly within one session. Including mtime and size in the key means
# reinstalling FreeRDP over the same path re-probes rather than returning the
# previous build's answer.
_webview_cache: dict[tuple[str, int, int], Webview] = {}


def detect_webview(binary: str | Path | None) -> Webview:
    """Determine whether a binary was built with WITH_WEBVIEW=ON.

    Blocks for as long as three subprocesses take, which for `nm` over a
    FreeRDP binary can be seconds. Never call it from a UI thread on a cold
    cache; see gui.WebviewProbe.
    """
    if not binary or not is_usable(binary):
        return Webview.UNKNOWN
    try:
        stat = Path(binary).stat()
    except OSError:
        return Webview.UNKNOWN

    key = (str(binary), stat.st_mtime_ns, stat.st_size)
    if key not in _webview_cache:
        _webview_cache[key] = _probe_webview(binary)
    return _webview_cache[key]


def _probe_webview(binary: str | Path) -> Webview:
    """Three probes, cheapest and most authoritative first:

    1. /buildconfig output, which names the build flags directly.
    2. Dynamic linkage against WebKitGTK.
    3. Defined symbols, catching a statically linked webview helper.
    """
    build = _run([str(binary), "/buildconfig"]).lower()
    if "with_webview" in build:
        # Matches "WITH_WEBVIEW=ON" and "-DWITH_WEBVIEW=ON" alike.
        for token in build.replace("\n", " ").split():
            if "with_webview" in token:
                return Webview.YES if token.endswith("on") else Webview.NO

    linked = _run(["ldd", str(binary)]).lower()
    if "webkit" in linked:
        return Webview.YES

    symbols = _run(["nm", "-C", "--defined-only", str(binary)], timeout=15).lower()
    if symbols:
        return Webview.YES if "webview" in symbols else Webview.NO

    return Webview.UNKNOWN


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

        tenant = clean_value(self.tenant_id)
        if tenant:
            args.append(f"/azure:tenantid:{tenant}")

        user = clean_value(self.username)
        if user:
            args.append(f"/u:{user}")

        for key, _label, flag, _default, _tip in TOGGLES:
            if self.toggles.get(key):
                args.append(expand_flag(flag))

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
