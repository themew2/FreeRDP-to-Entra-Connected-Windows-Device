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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .config import TOGGLES, clean_value, expand_flag

BIN_NAMES = ["sdl-freerdp", "sdl3-freerdp", "sdl-freerdp3"]

# Searched in order. All are *installed* prefixes.
#
# Binaries are deliberately never taken from a CMake build directory. Upstream
# guidance is to run `cmake --build <dir> --target install` and use the result;
# build-tree binaries pick up wrong library paths and resource locations.
PREFERRED_PATHS = [
    Path.home() / ".local/share/entrardp/freerdp/bin/sdl-freerdp",
    Path("/app/bin/sdl-freerdp"),          # Flatpak
    Path("/opt/freerdp-nightly/bin/sdl-freerdp"),  # upstream nightly packages
    Path("/usr/local/bin/sdl-freerdp"),
]

# Webview support landed upstream in this release; older builds cannot have it
# regardless of how they were configured.
WEBVIEW_MIN_VERSION = (3, 16, 0)


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


def detect_webview(binary: str | Path | None) -> Webview:
    """Determine whether a binary was built with WITH_WEBVIEW=ON.

    Three probes, cheapest and most authoritative first:
      1. /buildconfig output, which names the build flags directly.
      2. Dynamic linkage against WebKitGTK.
      3. Defined symbols, catching a statically linked webview helper.
    """
    if not binary or not is_usable(binary):
        return Webview.UNKNOWN

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
    extra: str = ""

    def command(self) -> list[str]:
        binary = clean_value(self.binary) or "sdl-freerdp"
        args: list[str] = []

        if self.force_x11:
            # The AAD webview popup does not map reliably on native Wayland.
            args += ["env", "SDL_VIDEODRIVER=x11"]
        args.append(binary)

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
        if not clean_value(self.host):
            issues.append("No host name set.")
        elif check_dns and not resolves(clean_value(self.host)):
            issues.append(
                f"'{clean_value(self.host)}' does not resolve via DNS or /etc/hosts. "
                "It must match the Entra-registered device name exactly."
            )
        if not clean_value(self.tenant_id):
            issues.append("No tenant ID set; Entra sign-in will likely fail.")
        if self.toggles.get("smart_sizing") and self.toggles.get("fullscreen"):
            issues.append(
                "Smart sizing with fullscreen causes rendering artifacts on Wayland."
            )
        return issues
