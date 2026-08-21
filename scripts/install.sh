#!/usr/bin/env bash
#
# One-shot installer: builds webview-enabled FreeRDP, installs the GUI,
# and registers the desktop entry.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- python

ensure_python_deps() {
    # Prefer distribution packages for pip and PyQt6. The PyQt6 wheel on PyPI
    # is a large compiled download, and the packaged build integrates better
    # with the system Qt theme.
    local missing=()
    python3 -m pip --version >/dev/null 2>&1 || missing+=(pip)
    python3 -c "import PyQt6.QtWidgets" >/dev/null 2>&1 || missing+=(pyqt6)

    [[ ${#missing[@]} -eq 0 ]] && return 0

    info "Installing Python prerequisites: ${missing[*]}"
    if command -v dnf >/dev/null; then
        local pkgs=()
        [[ " ${missing[*]} " == *" pip "*   ]] && pkgs+=(python3-pip)
        [[ " ${missing[*]} " == *" pyqt6 "* ]] && pkgs+=(python3-pyqt6)
        sudo dnf install -y "${pkgs[@]}"
    elif command -v apt-get >/dev/null; then
        local pkgs=()
        [[ " ${missing[*]} " == *" pip "*   ]] && pkgs+=(python3-pip)
        [[ " ${missing[*]} " == *" pyqt6 "* ]] && pkgs+=(python3-pyqt6)
        sudo apt-get update && sudo apt-get install -y "${pkgs[@]}"
    elif command -v pacman >/dev/null; then
        local pkgs=()
        [[ " ${missing[*]} " == *" pip "*   ]] && pkgs+=(python-pip)
        [[ " ${missing[*]} " == *" pyqt6 "* ]] && pkgs+=(python-pyqt6)
        sudo pacman -S --needed --noconfirm "${pkgs[@]}"
    else
        die "Install python3-pip and python3-pyqt6 with your package manager, then re-run."
    fi

    python3 -m pip --version >/dev/null 2>&1 || die "pip is still unavailable after installation."
}

pip_install() {
    # Many distributions mark the system Python as externally managed (PEP 668),
    # which blocks pip even for --user installs. Retry with the override, which
    # only ever touches ~/.local, never system packages.
    if python3 -m pip install --user --upgrade "$HERE" 2>/tmp/entrardp-pip.log; then
        return 0
    fi
    if grep -q "externally-managed-environment" /tmp/entrardp-pip.log; then
        warn "System Python is externally managed; installing into ~/.local anyway."
        python3 -m pip install --user --upgrade --break-system-packages "$HERE"
    else
        cat /tmp/entrardp-pip.log >&2
        die "Installation failed. See the output above."
    fi
}

# ----------------------------------------------------------------- main

if [[ "${SKIP_FREERDP:-0}" != "1" ]]; then
    info "Building FreeRDP with webview support"
    "$HERE/scripts/build-freerdp.sh"
else
    info "SKIP_FREERDP=1, using an existing FreeRDP build"
fi

ensure_python_deps

info "Installing the application"
pip_install

info "Registering desktop entry and icon"
install -Dm644 "$HERE/data/io.github.themew2.EntraRDP.desktop" \
    "$HOME/.local/share/applications/io.github.themew2.EntraRDP.desktop"
install -Dm644 "$HERE/data/io.github.themew2.EntraRDP.metainfo.xml" \
    "$HOME/.local/share/metainfo/io.github.themew2.EntraRDP.metainfo.xml"
# Both formats are installed. SVG is preferred where it works, but some icon
# renderers reject SVGs that Qt accepts, and a rejected icon silently falls
# back to a generic placeholder. PNGs at the standard sizes cannot fail that
# way, and the icon spec prefers an exact-size raster match anyway.
install -Dm644 "$HERE/data/icons/io.github.themew2.EntraRDP.svg" \
    "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.themew2.EntraRDP.svg"
for size in 16 22 24 32 48 64 128 256; do
    src="$HERE/data/icons/png/$size.png"
    [[ -f "$src" ]] || continue
    install -Dm644 "$src" \
        "$HOME/.local/share/icons/hicolor/${size}x${size}/apps/io.github.themew2.EntraRDP.png"
done

# A user icon directory without an index.theme is not a valid icon theme, and
# lookups skip it entirely. The icon file can be perfectly correct and still
# never resolve. System hicolor ships one; copy it if the user directory has
# none, otherwise write a minimal equivalent.
HICOLOR="$HOME/.local/share/icons/hicolor"
if [[ ! -f "$HICOLOR/index.theme" ]]; then
    if [[ -f /usr/share/icons/hicolor/index.theme ]]; then
        install -Dm644 /usr/share/icons/hicolor/index.theme "$HICOLOR/index.theme"
    else
        mkdir -p "$HICOLOR"
        cat > "$HICOLOR/index.theme" <<'THEME'
[Icon Theme]
Name=Hicolor
Comment=Fallback icon theme
Directories=16x16/apps,22x22/apps,24x24/apps,32x32/apps,48x48/apps,64x64/apps,128x128/apps,256x256/apps,scalable/apps

[16x16/apps]
Size=16
Type=Fixed
Context=Applications

[22x22/apps]
Size=22
Type=Fixed
Context=Applications

[24x24/apps]
Size=24
Type=Fixed
Context=Applications

[32x32/apps]
Size=32
Type=Fixed
Context=Applications

[48x48/apps]
Size=48
Type=Fixed
Context=Applications

[64x64/apps]
Size=64
Type=Fixed
Context=Applications

[128x128/apps]
Size=128
Type=Fixed
Context=Applications

[256x256/apps]
Size=256
Type=Fixed
Context=Applications

[scalable/apps]
Size=48
Type=Scalable
MinSize=8
MaxSize=512
Context=Applications
THEME
    fi
    info "Created $HICOLOR/index.theme (icon lookups skip directories without one)"
fi
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
# Icon lookups consult icon-theme.cache in preference to scanning the
# directory. A cache written before this icon was installed records its
# absence and keeps returning that, so a correctly placed file still resolves
# to a generic placeholder. Removing the cache before regenerating is more
# reliable than -f alone, particularly in ~/.local/share/icons/hicolor, which
# other software (Steam, for one) also writes to.
rm -f "$HOME/.local/share/icons/hicolor/icon-theme.cache"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
kbuildsycoca6 --noincremental 2>/dev/null || kbuildsycoca5 --noincremental 2>/dev/null || true

if [[ ! -x "$HOME/.local/bin/entrardp" ]]; then
    warn "Expected launcher at ~/.local/bin/entrardp was not created."
    warn "Run it with: python3 -m entrardp"
fi

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) warn "Add ~/.local/bin to your PATH, then open a new shell." ;;
esac

info "Done. Launch 'Entra RDP' from your application menu, or run: entrardp"
