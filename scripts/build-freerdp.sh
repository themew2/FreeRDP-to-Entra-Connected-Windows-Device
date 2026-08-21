#!/usr/bin/env bash
#
# Builds FreeRDP with WITH_WEBVIEW=ON and installs it privately for Entra RDP.
#
# Distro packages typically enable WITH_AAD, WITH_PULSE and WITH_SSO_MIB but
# leave WITH_WEBVIEW=OFF, which downgrades Entra sign-in to a copy-a-URL flow.
# FreeRDP fetches its webview helper via CMake FetchContent, which most
# packaging policies forbid, so a source build is the practical way to get it.
# Requires FreeRDP 3.16.0 or newer.
#
# Installs to ~/.local/share/entrardp/freerdp — a private prefix, so it will
# not collide with or shadow any system FreeRDP package.

set -euo pipefail

PREFIX="${ENTRARDP_PREFIX:-$HOME/.local/share/entrardp/freerdp}"
SRC="${ENTRARDP_SRC:-$HOME/.cache/entrardp/FreeRDP}"
JOBS="${JOBS:-$(nproc)}"
BRANCH="${FREERDP_BRANCH:-master}"
# Note: webview support requires FreeRDP 3.16.0 or newer. Building from a
# recent branch satisfies this; the post-install check confirms the result.
# Automatic token retrieval via a local identity broker. Optional, and not
# required for the webview sign-in flow. "auto" enables it only when the
# sso-mib library is actually present; set ON or OFF to force either way.
WITH_SSO_MIB="${WITH_SSO_MIB:-auto}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- deps

install_deps_dnf() {
    info "Installing build dependencies (dnf)"
    # webkitgtk6.0-devel is the critical one: FreeRDP's webview helper looks
    # for webkitgtk-6.0, then webkit2gtk-4.1, then webkit2gtk-4.0. Current
    # Fedora has deprecated 4.0, and the GTK4-based 6.0 works correctly.
    sudo dnf install -y \
        cmake ninja-build gcc-c++ git \
        systemd-devel libuuid-devel pulseaudio-libs-devel \
        libXrandr-devel gsm-devel pam-devel fuse3-devel \
        opus-devel lame-devel openssl-devel libX11-devel \
        libXext-devel libXinerama-devel libXcursor-devel \
        libXi-devel libXdamage-devel libXv-devel libxkbfile-devel \
        libxkbcommon-devel jansson-devel \
        alsa-lib-devel openh264-devel \
        libusb1-devel uriparser-devel \
        SDL3-devel SDL3_ttf-devel SDL3_image-devel \
        pkcs11-helper-devel krb5-devel cjson-devel cairo-devel \
        soxr-devel wayland-devel wayland-protocols-devel \
        cups-devel webkitgtk6.0-devel

    # FFmpeg is requested by pkg-config capability rather than package name.
    #
    # Fedora ships patent-stripped libav*-free packages; RPMFusion ships the
    # full ffmpeg. The two conflict, so hardcoding either set breaks whichever
    # machine has the other. Asking for the capability lets dnf resolve to
    # whatever provider is already installed instead of forcing a swap.
    #
    # This matters: resolving it with --allowerasing would silently replace a
    # user's RPMFusion ffmpeg with the stripped build, degrading codec support
    # system-wide as a side effect of building an RDP client.
    info "Installing FFmpeg development headers"
    sudo dnf install -y \
        "pkgconfig(libavcodec)" \
        "pkgconfig(libavformat)" \
        "pkgconfig(libavutil)" \
        "pkgconfig(libswresample)" \
        "pkgconfig(libswscale)"

    # Optional: automatic token retrieval through a local identity broker.
    # Packaged on some distributions and not others, so this is best-effort
    # and deliberately not allowed to fail the run.
    sudo dnf install -y --skip-unavailable sso-mib-devel 2>/dev/null || true
}

install_deps_apt() {
    warn "The Debian/Ubuntu package list is UNTESTED. Preflight will verify the result."
    info "Installing build dependencies (apt)"
    sudo apt-get update
    sudo apt-get install -y \
        cmake ninja-build g++ git pkg-config \
        libsystemd-dev uuid-dev libpulse-dev libxrandr-dev libgsm1-dev \
        libpam0g-dev libfuse3-dev libopus-dev libmp3lame-dev libssl-dev \
        libx11-dev libxext-dev libxinerama-dev libxcursor-dev libxi-dev \
        libxdamage-dev libxv-dev libxkbfile-dev libxkbcommon-dev \
        libjansson-dev libasound2-dev \
        libavcodec-dev libavformat-dev libavutil-dev libswresample-dev \
        libswscale-dev libusb-1.0-0-dev liburiparser-dev \
        libsdl3-dev libsdl3-ttf-dev libsdl3-image-dev \
        libpkcs11-helper1-dev libkrb5-dev libcjson-dev libcairo2-dev \
        libsoxr-dev libwayland-dev wayland-protocols libcups2-dev \
        libwebkitgtk-6.0-dev
    # Optional, packaged on some distributions only.
    sudo apt-get install -y libsso-mib-dev 2>/dev/null || true
}

install_deps_pacman() {
    warn "The Arch package list is UNTESTED. Preflight will verify the result."
    info "Installing build dependencies (pacman)"
    sudo pacman -S --needed --noconfirm \
        cmake ninja gcc git pkgconf systemd-libs util-linux-libs \
        libpulse libxrandr gsm pam fuse3 opus lame openssl libx11 \
        libxext libxinerama libxcursor libxi libxdamage libxv libxkbfile \
        libxkbcommon jansson \
        alsa-lib ffmpeg libusb uriparser sdl3 sdl3_ttf sdl3_image \
        pkcs11-helper krb5 cjson cairo soxr wayland wayland-protocols \
        cups webkitgtk-6.0
    # Optional, may only be available from the AUR.
    sudo pacman -S --needed --noconfirm sso-mib 2>/dev/null || true
}

install_deps() {
    if [[ "${SKIP_DEPS:-0}" == "1" ]]; then
        warn "SKIP_DEPS=1 set, not installing dependencies"
        return
    fi
    # A dependency install can legitimately fail on a system with third-party
    # repositories and still leave everything needed already present. Let
    # check_deps make the final call rather than aborting here.
    set +e
    if   command -v dnf    >/dev/null; then install_deps_dnf
    elif command -v apt-get>/dev/null; then install_deps_apt
    elif command -v pacman >/dev/null; then install_deps_pacman
    else
        set -e
        warn "No supported package manager found (dnf, apt, pacman)."
        warn ""
        warn "Install development packages providing these pkg-config modules,"
        warn "then re-run with SKIP_DEPS=1:"
        warn "  webkitgtk-6.0   (or webkit2gtk-4.1)   <- required for WITH_WEBVIEW"
        warn "  sdl3, sdl3-ttf, sdl3-image"
        warn "  openssl, libpulse, krb5, zlib, xkbcommon"
        warn "  jansson (or json-c / cjson)"
        warn "  libavcodec, libavformat, libavutil, libswresample, libswscale"
        warn "  liburiparser, cairo, libusb-1.0, cups, wayland, wayland-protocols"
        warn ""
        warn "Plus: cmake, ninja, a C/C++ compiler, git, pkg-config."
        die "Cannot install dependencies automatically on this system."
    fi
    local rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        warn "Dependency installation reported errors. Checking what is actually present..."
    fi
}


# ------------------------------------------------------------ preflight

# Verified via pkg-config rather than the package manager, so it works the same
# on every distribution and catches renamed packages, partial installs, and
# SKIP_DEPS=1 runs. Reports everything missing at once instead of failing one
# library at a time across repeated cmake runs.
check_deps() {
    local missing_tools=() missing_libs=() webkit_found=""

    info "Checking build prerequisites"

    for tool in cmake ninja git pkg-config; do
        command -v "$tool" >/dev/null || missing_tools+=("$tool")
    done
    command -v g++ >/dev/null || command -v c++ >/dev/null || missing_tools+=("g++")

    # WebKitGTK is the one that decides whether webview can be enabled at all.
    # FreeRDP's helper probes these three names in this order.
    for wk in webkitgtk-6.0 webkit2gtk-4.1 webkit2gtk-4.0; do
        if pkg-config --exists "$wk" 2>/dev/null; then
            webkit_found="$wk"
            break
        fi
    done

    # Libraries whose absence silently disables a feature we depend on, rather
    # than producing an obvious hard error.
    #
    # Each entry is "pc-name[|alternate-name...]:Human label". Alternates exist
    # because pkg-config file names are not standardised across distributions:
    # uriparser ships as liburiparser.pc on some and uriparser.pc on others,
    # and the same library can be present under either name.
    local required=(
        "openssl|libssl:OpenSSL"
        "libpulse:PulseAudio"
        "sdl3|SDL3:SDL3"
        "krb5|mit-krb5|krb5-gssapi:Kerberos"
        "libcjson|cjson:cJSON"
        "liburiparser|uriparser:uriparser"
        "zlib:zlib"
        # Required by uwac; Wayland is a mandatory feature, so a missing
        # xkbcommon stops configure outright rather than degrading gracefully.
        "xkbcommon:xkbcommon"
        # FreeRDP probes jansson, then json-c, then cJSON. Entra token handling
        # needs a JSON parser, so treat the absence of all three as fatal.
        "jansson|json-c|libcjson|cjson:a JSON library (jansson preferred)"
        "libavcodec:FFmpeg libavcodec"
        "libavutil:FFmpeg libavutil"
        "libswresample:FFmpeg libswresample"
    )
    for entry in "${required[@]}"; do
        local names="${entry%%:*}" label="${entry##*:}" found=0
        local IFS='|'
        for pc in $names; do
            if pkg-config --exists "$pc" 2>/dev/null; then
                found=1
                break
            fi
        done
        unset IFS
        [[ $found -eq 1 ]] || missing_libs+=("$label (tried: ${names//|/, })")
    done

    local failed=0

    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        warn "Missing build tools: ${missing_tools[*]}"
        failed=1
    fi

    if [[ -z "$webkit_found" ]]; then
        warn "No WebKitGTK development package found (tried webkitgtk-6.0, webkit2gtk-4.1, webkit2gtk-4.0)."
        warn "  Without it WITH_WEBVIEW cannot be enabled, which is the entire point of this build."
        warn "  Fedora: sudo dnf install webkitgtk6.0-devel"
        warn "  Debian/Ubuntu: sudo apt install libwebkitgtk-6.0-dev"
        warn "  Arch: sudo pacman -S webkitgtk-6.0"
        failed=1
    else
        info "WebKitGTK found: $webkit_found"
    fi

    if [[ ${#missing_libs[@]} -gt 0 ]]; then
        warn "Missing development libraries:"
        printf '    - %s\n' "${missing_libs[@]}" >&2
        failed=1
    fi

    if [[ $failed -eq 1 ]]; then
        if [[ "${SKIP_PREFLIGHT:-0}" == "1" ]]; then
            # pkg-config file names vary between distributions, so this check
            # can report a library as missing when it is present under a name
            # not listed above. The override exists so a naming gap here never
            # blocks a build that would otherwise succeed; cmake remains the
            # final authority.
            warn "SKIP_PREFLIGHT=1 set, continuing despite the above."
            return 0
        fi
        warn ""
        warn "If you believe a library above is actually installed, its pkg-config"
        warn "name may differ on your distribution. Check with:"
        warn "    pkg-config --list-all | grep -i <library>"
        warn "and re-run with SKIP_PREFLIGHT=1 to continue anyway."
        die "Prerequisites missing."
    fi

    info "All prerequisites present"
}

# ------------------------------------------------------------- source

fetch_source() {
    if [[ -d "$SRC/.git" ]]; then
        info "Updating existing checkout at $SRC"
        git -C "$SRC" fetch --depth 1 origin "$BRANCH"
        git -C "$SRC" reset --hard FETCH_HEAD
    else
        info "Cloning FreeRDP ($BRANCH) into $SRC"
        mkdir -p "$(dirname "$SRC")"
        git clone --depth 1 --branch "$BRANCH" https://github.com/FreeRDP/FreeRDP.git "$SRC"
    fi
}

# -------------------------------------------------------------- build

resolve_sso_mib() {
    [[ "$WITH_SSO_MIB" != "auto" ]] && return 0
    if pkg-config --atleast-version=0.5.0 sso-mib 2>/dev/null; then
        WITH_SSO_MIB=ON
        info "sso-mib found, enabling WITH_SSO_MIB"
    else
        WITH_SSO_MIB=OFF
        info "sso-mib not found, building without it (optional; webview sign-in is unaffected)"
    fi
}

configure_and_build() {
    local build="$SRC/build"
    resolve_sso_mib
    mkdir -p "$build"
    info "Configuring (prefix: $PREFIX)"
    cmake -S "$SRC" -B "$build" -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" \
        -DWITH_WEBVIEW=ON \
        -DWITH_AAD=ON \
        -DWITH_SSO_MIB="$WITH_SSO_MIB" \
        -DWITH_CLIENT_SDL=ON \
        -DWITH_CLIENT_SDL3=ON \
        -DWITH_CLIENT_SDL2=OFF \
        -DWITH_PULSE=ON \
        -DWITH_X11=ON \
        -DWITH_WAYLAND=ON \
        -DWITH_FFMPEG=ON \
        -DWITH_CUPS=ON \
        -DBUILD_TESTING=OFF

    # WITH_WEBVIEW only becomes a real option once the SDL client subdirectory
    # is processed. If SDL got disabled, cmake silently ignores the flag and
    # the resulting binary looks fine but has no popup. Fail loudly instead.
    if ! cmake -L "$build" 2>/dev/null | grep -q "WITH_WEBVIEW:BOOL=ON"; then
        die "WITH_WEBVIEW did not initialise. WebKitGTK development headers are probably missing."
    fi
    # WITH_PULSE defaults to OFF upstream and silently stays off when the
    # PulseAudio headers are missing at configure time. Distro packages enable
    # it, so a source build that quietly loses audio is a surprise worth
    # catching here rather than mid-call.
    if ! cmake -L "$build" 2>/dev/null | grep -q "WITH_PULSE:BOOL=ON"; then
        die "WITH_PULSE did not initialise. Install the PulseAudio development headers and re-run."
    fi

    info "Building with $JOBS jobs (this takes a while)"
    cmake --build "$build" -j "$JOBS"
    # Upstream guidance: install the build, never run binaries out of the build
    # directory. Build-tree binaries resolve libraries and resources wrongly.
    info "Installing to $PREFIX"
    cmake --build "$build" --target install
}

verify() {
    local bin="$PREFIX/bin/sdl-freerdp"
    [[ -x "$bin" ]] || die "Expected binary missing at $bin"
    if "$bin" /buildconfig 2>&1 | tr ' ' '\n' | grep -qi "WITH_WEBVIEW=ON"; then
        info "Verified: WITH_WEBVIEW=ON"
    elif ldd "$bin" 2>/dev/null | grep -qi webkit; then
        info "Verified: linked against WebKitGTK"
    else
        warn "Could not confirm webview support. Sign-in may fall back to the URL flow."
    fi
    info "Binary ready at $bin"
}

main() {
    [[ $EUID -eq 0 ]] && die "Do not run this as root; it installs into your home directory."
    install_deps
    check_deps
    fetch_source
    configure_and_build
    verify
}

main "$@"
