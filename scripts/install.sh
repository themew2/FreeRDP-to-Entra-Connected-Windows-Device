#!/usr/bin/env bash
#
# Installer: builds a webview-enabled FreeRDP, installs the GUI, and registers
# the desktop entry.
#
# FreeRDP is built through upstream's RPM packaging where that is available,
# and from source otherwise. Set SKIP_FREERDP=1 if you already have a build
# with WITH_WEBVIEW=ON.

set -euo pipefail
# The repository root, not the script directory: pip installs from it and the
# build scripts are addressed relative to it.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/freerdp-common.sh
source "$HERE/scripts/freerdp-common.sh"   # info/warn/die

# ----------------------------------------------------------------- python

ensure_python_deps() {
    # Prefer distribution packages for pip and PyQt6. The PyQt6 wheel on PyPI
    # is a large compiled download, and the packaged build integrates better
    # with the system Qt theme.
    local missing=()
    python3 -m pip --version >/dev/null 2>&1 || missing+=(pip)
    python3 -c "import PyQt6.QtWidgets" >/dev/null 2>&1 || missing+=(pyqt6)

    [[ ${#missing[@]} -eq 0 ]] && return 0

    info "Installing Python prerequisites: ${missing[*]}"
    local pkgs=()
    if command -v dnf >/dev/null; then
        [[ " ${missing[*]} " == *" pip "*   ]] && pkgs+=(python3-pip)
        [[ " ${missing[*]} " == *" pyqt6 "* ]] && pkgs+=(python3-pyqt6)
        sudo dnf install -y "${pkgs[@]}"
    elif command -v apt-get >/dev/null; then
        [[ " ${missing[*]} " == *" pip "*   ]] && pkgs+=(python3-pip)
        [[ " ${missing[*]} " == *" pyqt6 "* ]] && pkgs+=(python3-pyqt6)
        sudo apt-get update && sudo apt-get install -y "${pkgs[@]}"
    elif command -v pacman >/dev/null; then
        [[ " ${missing[*]} " == *" pip "*   ]] && pkgs+=(python-pip)
        [[ " ${missing[*]} " == *" pyqt6 "* ]] && pkgs+=(python-pyqt6)
        sudo pacman -S --needed --noconfirm "${pkgs[@]}"
    else
        die "Install python3-pip and python3-pyqt6 with your package manager, then re-run."
    fi

    python3 -m pip --version >/dev/null 2>&1 || die "pip is still unavailable after installation."
}

pip_install() {
    # mktemp rather than a fixed /tmp/entrardp-pip.log: a predictable name in a
    # shared directory is one another user can pre-create as a symlink, and on
    # a multi-user machine the fixed path also collides between users.
    local log rc=0
    log="$(mktemp -t entrardp-pip.XXXXXX)"

    # Many distributions mark the system Python as externally managed (PEP 668),
    # which blocks pip even for --user installs. Retry with the override, which
    # only ever touches ~/.local, never system packages.
    if python3 -m pip install --user --upgrade "$HERE" 2>"$log"; then
        rm -f "$log"
        return 0
    fi
    grep -q "externally-managed-environment" "$log" || rc=1
    # Removed before either branch below, because die exits and would leak it.
    [[ $rc -eq 0 ]] || cat "$log" >&2
    rm -f "$log"

    [[ $rc -eq 0 ]] || die "Installation failed. See the output above."
    warn "System Python is externally managed; installing into ~/.local anyway."
    python3 -m pip install --user --upgrade --break-system-packages "$HERE"
}

# ---------------------------------------------------------------- freerdp

build_freerdp() {
    if [[ "${SKIP_FREERDP:-0}" == "1" ]]; then
        info "SKIP_FREERDP=1, using an existing FreeRDP build"
        return 0
    fi

    if command -v rpmbuild >/dev/null && command -v dnf >/dev/null; then
        # Preferred route. Builds through FreeRDP's own packaging, so the
        # BuildRequires list comes from upstream rather than being maintained
        # here, and the result is a package dnf can track and remove.
        info "Building a webview-enabled FreeRDP package"
        "$HERE/scripts/build-freerdp-rpm.sh"
        return 0
    fi

    if [[ -x "$HERE/scripts/build-freerdp.sh" ]]; then
        # Fallback for non-RPM systems. The dependency lists in that script are
        # maintained here and have only been exercised on Fedora, so expect to
        # install a package or two by hand; its preflight check names them.
        warn "No RPM tooling found; falling back to the source build."
        warn "Its dependency lists are untested outside Fedora. Preflight will"
        warn "report anything missing before the compile starts."
        "$HERE/scripts/build-freerdp.sh"
        return 0
    fi

    warn "No FreeRDP build script available for this system."
    warn "Install or build a FreeRDP client with WITH_WEBVIEW=ON and select it"
    warn "in the application. Check an existing install with:"
    warn "    sdl-freerdp /buildconfig | tr ' ' '\\n' | grep -i WITH_WEBVIEW"
    info "Continuing with the GUI installation."
}

# ------------------------------------------------------------------- main

build_freerdp
ensure_python_deps

info "Installing the application"
pip_install

info "Registering desktop entry and icon"
install -Dm644 "$HERE/data/io.github.themew2.EntraRDP.desktop" \
    "$HOME/.local/share/applications/io.github.themew2.EntraRDP.desktop"
install -Dm644 "$HERE/data/io.github.themew2.EntraRDP.metainfo.xml" \
    "$HOME/.local/share/metainfo/io.github.themew2.EntraRDP.metainfo.xml"
install -Dm644 "$HERE/data/icons/io.github.themew2.EntraRDP.svg" \
    "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.themew2.EntraRDP.svg"

# Both formats are installed. SVG is preferred where it works, but some icon
# renderers reject SVGs that Qt accepts, and a rejected icon silently falls
# back to a generic placeholder. PNGs at the standard sizes cannot fail that
# way, and the icon spec prefers an exact-size raster match anyway.
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
rm -f "$HICOLOR/icon-theme.cache"
gtk-update-icon-cache -f -t "$HICOLOR" 2>/dev/null || true
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
