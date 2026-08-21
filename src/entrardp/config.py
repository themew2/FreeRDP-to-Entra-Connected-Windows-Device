"""Configuration, flag definitions, and profile persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_ID = "io.github.themew2.EntraRDP"
APP_NAME = "Entra RDP"

# Flatpak exports XDG_CONFIG_HOME into the sandbox; honour it either way.
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "entrardp"
CONFIG_FILE = CONFIG_DIR / "profiles.json"

# Session option toggles: key, label, flag, default, tooltip.
#
# Defaults reflect the connection profile verified working against an
# Entra-joined host. Anything not in that verified set defaults to off.
TOGGLES: list[tuple[str, str, str, bool, str]] = [
    ("cert_ignore", "Ignore certificate warnings", "/cert:ignore", True,
     "Skips certificate validation. Reasonable for known internal hosts."),
    ("fullscreen", "Fullscreen", "/f", True,
     "Start the session fullscreen. Toggle at runtime with Right Shift + Enter."),
    ("dynamic_res", "Dynamic resolution", "/dynamic-resolution", False,
     "Session resizes with the window. Mutually exclusive with smart sizing."),
    ("workarea", "Fit to work area", "/workarea", False,
     "Sizes to the usable screen area, excluding panels and docks."),
    ("smart_sizing", "Smart sizing (scale)", "/smart-sizing", False,
     ("Scales the remote desktop to the window.\n"
      "Known issue: horizontal line artifacts with fullscreen on Wayland.")),
    ("multimon", "Use all monitors", "/multimon", False,
     "Span the session across every attached display."),
    ("sound", "Speaker redirection", "/sound:sys:pulse", True,
     "Remote audio output through PulseAudio or PipeWire."),
    ("microphone", "Microphone redirection", "/microphone:sys:pulse", True,
     "Local microphone into the remote session. Required for Teams calls."),
    ("clipboard", "Clipboard sharing", "/clipboard", True,
     "Copy and paste between local and remote."),
    ("home_drive", "Redirect home folder", "/drive:home,%HOME%", False,
     "Mounts your home directory as a drive inside the session."),
    ("printers", "Redirect printers", "/printer", False,
     "Exposes local CUPS printers to the remote host."),
    ("gfx_avc", "H.264 graphics (AVC444)", "/gfx:AVC444", False,
     "Lower bandwidth over slow links. Requires remote-side support."),
    ("compression", "Bulk compression", "/compression", False,
     "Reduces bandwidth at a small CPU cost."),
]

# Pairs of toggle keys that FreeRDP refuses to accept together.
MUTUALLY_EXCLUSIVE: list[tuple[str, str]] = [
    ("dynamic_res", "smart_sizing"),
    ("workarea", "fullscreen"),
]


def find_icon() -> str | None:
    """Locate the application icon.

    Checked in order: the copy bundled inside the installed Python package,
    then the standard icon theme directories. The bundled copy means the icon
    works even when the app is run straight from a source checkout, before
    anything has been installed into a theme directory.
    """
    # PNG first: some renderers reject SVGs that Qt itself accepts, so the
    # raster copy is the more dependable default.
    for name in ("icon.png", "icon.svg"):
        bundled = Path(__file__).parent / "data" / name
        if bundled.is_file():
            return str(bundled)

    xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    candidates = [
        xdg_data_home / f"icons/hicolor/scalable/apps/{APP_ID}.svg",
        Path(f"/app/share/icons/hicolor/scalable/apps/{APP_ID}.svg"),
        Path(f"/usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg"),
        Path(f"/usr/local/share/icons/hicolor/scalable/apps/{APP_ID}.svg"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def clean_value(text: str | None) -> str:
    """Strip whitespace and stray quote characters from a field value.

    Pasting from portals, docs, or shell snippets routinely drags along quote
    characters or non-breaking spaces. FreeRDP's command line parser treats an
    unbalanced quote as fatal, failing before it ever attempts a connection.
    """
    if not text:
        return ""
    cleaned = text.strip().replace("\u00a0", " ").strip()
    while len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned.replace('"', "").replace("'", "").strip()


def expand_flag(flag: str) -> str:
    """Substitute runtime placeholders inside a flag template."""
    return flag.replace("%HOME%", str(Path.home())).replace(
        "%USER%", os.environ.get("USER", "user")
    )


class ProfileStore:
    """Profiles persisted as JSON, readable only by the owner.

    Stores tenant IDs, usernames, and hostnames. No secrets: authentication
    happens entirely through the Entra webview and nothing is cached here.
    """

    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path
        self.profiles: dict[str, dict] = {}
        self.load()

    def load(self) -> dict[str, dict]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if isinstance(data, dict):
                    self.profiles = data
            except (json.JSONDecodeError, OSError):
                self.profiles = {}
        return self.profiles

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.profiles, indent=2, sort_keys=True))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def put(self, name: str, data: dict) -> None:
        self.profiles[name] = data
        self.save()

    def delete(self, name: str) -> None:
        self.profiles.pop(name, None)
        self.save()

    def names(self) -> list[str]:
        return sorted(self.profiles)
