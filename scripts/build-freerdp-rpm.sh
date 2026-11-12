#!/usr/bin/env bash
#
# Builds an RPM of FreeRDP with Entra sign-in windows enabled, using upstream's
# own packaging.
#
# This is the route recommended by the FreeRDP maintainers for RPM-based
# distributions (FreeRDP#13237): modify packaging/rpm/freerdp-nightly.spec to
# enable sign-in, then build with packaging/scripts/create_rpm.sh. On FreeRDP
# 3.32+ that means the browser and embedded-window sign-in helpers; before
# that, WITH_WEBVIEW. Only needed when the distribution's own FreeRDP has no
# sign-in helper, which install.sh checks first.
#
# Preferred over scripts/build-freerdp.sh where it works, because:
#   - dnf owns the result, so `dnf remove freerdp-nightly` cleanly uninstalls
#   - the BuildRequires list is maintained upstream, not guessed at here
#   - installs to /opt/freerdp-nightly, coexisting with the distro package
#
# The source is always a tagged release, the same as scripts/build-freerdp.sh.
# Only the packaging recipe comes from upstream's "nightly" spec; the name
# refers to how upstream publishes test packages, not to the branch built.
#
# That spec is tuned for nightly testing rather than daily use: clang,
# CMAKE_BUILD_TYPE=Debug, -O1, AddressSanitizer, WITH_VERBOSE_WINPR_ASSERT and
# experimental VAAPI H264 encoding, plus the test suite. Those defaults cost
# real runtime performance, and a build carrying them produced audibly
# degraded microphone capture in Teams calls over AUDIN where a plain Release
# build of the same release tag did not.
#
# So this script turns them off by default. Set RELEASE_BUILD=0 to build the
# spec as upstream ships it, which is only useful when reporting a bug to the
# FreeRDP project.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/freerdp-common.sh
source "$HERE/freerdp-common.sh"   # info/warn/die, resolve_version, sign-in flags

SRC="${ENTRARDP_RPM_SRC:-$HOME/.cache/entrardp/FreeRDP-rpm}"
# Set by resolve_version(): the newest release tag, or FREERDP_BRANCH if set.
BRANCH=""
# Defaults on: debug instrumentation is for diagnosing FreeRDP itself, not
# for a client someone takes calls on.
RELEASE_BUILD="${RELEASE_BUILD:-1}"
AUTO_INSTALL="${AUTO_INSTALL:-1}"

[[ $EUID -eq 0 ]] && die "Do not run this as root."
command -v rpmbuild >/dev/null || command -v dnf >/dev/null || \
    die "This script requires an RPM-based distribution. Use scripts/build-freerdp.sh instead."

# ------------------------------------------------------------- prerequisites

info "Installing RPM build tooling"
sudo dnf install -y rpm-build rpmdevtools git clang dnf-plugins-core

# --------------------------------------------------------------- get sources

resolve_version

if [[ -d "$SRC/.git" ]]; then
    info "Updating existing checkout at $SRC"
    git -C "$SRC" fetch --depth 1 origin "$BRANCH"
    git -C "$SRC" reset --hard FETCH_HEAD
    git -C "$SRC" clean -fd
else
    info "Cloning FreeRDP ($BRANCH) into $SRC"
    mkdir -p "$(dirname "$SRC")"
    git clone --depth 1 --branch "$BRANCH" https://github.com/FreeRDP/FreeRDP.git "$SRC"
fi

SPEC="$SRC/packaging/rpm/freerdp-nightly.spec"
CREATE="$SRC/packaging/scripts/create_rpm.sh"
[[ -f "$SPEC"   ]] || die "Spec file not found at $SPEC (upstream layout may have changed)."
[[ -f "$CREATE" ]] || die "create_rpm.sh not found at $CREATE (upstream layout may have changed)."

# ----------------------------------------------------------------- patch spec

# Sets a cmake option in the spec's %build arguments.
#
# The arguments are indented continuation lines, so matches anchor on optional
# leading whitespace rather than the start of the line, and a hyphen is never
# escaped: GNU grep warns about the stray backslash, and the leading
# [[:space:]]* already stops the pattern being read as an option.
#
# An existing -D$1=... line is rewritten in place. A missing one is added
# after the line named by $ANCHOR, reusing that line's own trailing
# continuation through the second capture group. That keeps a literal
# backslash out of this script, which avoids shellcheck SC1003 and sidesteps
# sed eating a trailing backslash in an append block.
spec_set() {
    local name=$1 value=$2
    if grep -qE "^[[:space:]]*-D${name}=" "$SPEC"; then
        sed -i -E "s/^([[:space:]]*)-D${name}=[A-Za-z0-9]+/\\1-D${name}=${value}/" "$SPEC"
    elif grep -qE "^[[:space:]]*${ANCHOR}[[:space:]]+.\$" "$SPEC"; then
        sed -i -E "s|^([[:space:]]*)${ANCHOR}(.*)\$|\\1${ANCHOR}\\2\\n\\1-D${name}=${value}\\2|" "$SPEC"
    else
        # Only reachable if the anchor ends the argument list. Appending
        # there would orphan the new line outside the cmake invocation.
        warn "Could not place -D${name}=${value} in the spec; set it by hand in $SPEC."
        return 1
    fi
}

# Which sign-in mechanism this tree has decides what to switch on. The spec
# may still carry -DWITH_WEBVIEW=OFF on 3.32+ trees, where that option no
# longer exists: rewriting it to ON would succeed and change nothing, which
# is how a build silently ends up with no sign-in window at all.
resolve_signin_flags "$SRC"
if [[ ${#SIGNIN_HELPERS[@]} -gt 0 ]]; then
    # The dead WITH_WEBVIEW line becomes the embedded helper, so it can
    # serve as the anchor for everything added below.
    if grep -qE '^[[:space:]]*-DWITH_WEBVIEW=' "$SPEC"; then
        sed -i -E 's/^([[:space:]]*)-DWITH_WEBVIEW=[A-Za-z0-9]+/\1-DWITH_WEBVIEW_AAD_AUTH_HELPER=ON/' "$SPEC"
    fi
    ANCHOR='-DWITH_WEBVIEW_AAD_AUTH_HELPER=ON'
    grep -qE "^[[:space:]]*${ANCHOR}" "$SPEC" \
        || ANCHOR='-DWITH_XDG_AAD_AUTH_HELPER=ON'
    grep -qE "^[[:space:]]*-DWITH_(WEBVIEW|XDG)_AAD_AUTH_HELPER=" "$SPEC" \
        || die "No sign-in option found in the spec to anchor on. Inspect $SPEC and set it by hand."
    info "Enabling the browser and embedded-window sign-in helpers in the spec"
    spec_set WITH_XDG_AAD_AUTH_HELPER ON || die "Could not enable the browser sign-in helper."
    spec_set WITH_WEBVIEW_AAD_AUTH_HELPER ON || die "Could not enable the embedded sign-in helper."
else
    # FreeRDP before 3.32: the in-process webview, a single-line change.
    if grep -qE '^[[:space:]]*-DWITH_WEBVIEW=OFF' "$SPEC"; then
        info "Enabling WITH_WEBVIEW in the spec"
        sed -i -E 's/^([[:space:]]*)-DWITH_WEBVIEW=OFF/\1-DWITH_WEBVIEW=ON/' "$SPEC"
    elif grep -qE '^[[:space:]]*-DWITH_WEBVIEW=ON' "$SPEC"; then
        info "Spec already has WITH_WEBVIEW=ON"
    else
        die "No WITH_WEBVIEW line found in the spec. Inspect $SPEC and set it by hand."
    fi
    ANCHOR='-DWITH_WEBVIEW=ON'
fi

if [[ "$RELEASE_BUILD" == "1" ]]; then
    info "Switching to a Release build without debug instrumentation"
    sed -i -E 's/^([[:space:]]*)-DCMAKE_BUILD_TYPE=Debug/\1-DCMAKE_BUILD_TYPE=Release/' "$SPEC"
    sed -i -E 's/^([[:space:]]*)-DWITH_SANITIZE_ADDRESS=ON/\1-DWITH_SANITIZE_ADDRESS=OFF/' "$SPEC"
    sed -i -E 's/^([[:space:]]*)-DCMAKE_C_FLAGS="-O1"/\1-DCMAKE_C_FLAGS="-O2"/' "$SPEC"
    sed -i -E 's/^([[:space:]]*)-DCMAKE_CXX_FLAGS="-O1"/\1-DCMAKE_CXX_FLAGS="-O2"/' "$SPEC"

    # WITH_VERBOSE_WINPR_ASSERT and WITH_VAAPI_H264_ENCODING are FreeRDP cmake
    # defaults rather than spec settings, so they survive the build type
    # change and have to be turned off explicitly. The client announces both
    # on every connection:
    #
    #   This build is using [experimental] build options:
    #   * 'WITH_VAAPI_H264_ENCODING=ON'
    #   [experimental] build options might crash the application
    #
    #   This build is using [runtime-check] build options:
    #   * 'WITH_VERBOSE_WINPR_ASSERT=ON'
    #   [runtime-check] build options might slow down the application
    #
    # Verbose asserts run on hot paths including audio capture, where missing
    # a deadline is audible. VAAPI H264 encoding is encode-only and unused by
    # a client, so disabling it costs nothing.
    #
    # Neither appears in the spec today, so spec_set adds them after the
    # sign-in line; if either ever does, it is rewritten in place instead.
    # A failure here leaves a slower client, not a broken one, so it warns.
    spec_set WITH_VERBOSE_WINPR_ASSERT OFF || true
    spec_set WITH_VAAPI_H264_ENCODING OFF || true
else
    warn "RELEASE_BUILD=0: building upstream's nightly test configuration."
    warn "Expect reduced performance and degraded audio. Use this only when"
    warn "reproducing a bug for the FreeRDP project."
fi

info "Resulting build flags:"
# `|| true` because this only reports state. grep exits 1 when it matches
# nothing, and under `set -euo pipefail` that would abort the build from a
# line whose only job is to print a summary.
grep -E '^[[:space:]]*-D(WITH_WEBVIEW|WITH_[A-Z]+_AAD_AUTH_HELPER|CMAKE_BUILD_TYPE|WITH_SANITIZE_ADDRESS|WITH_VERBOSE_WINPR_ASSERT|WITH_VAAPI_H264_ENCODING)' "$SPEC" || true

# ---------------------------------------------------------------- build rpm

# Install the spec's declared build dependencies before building.
#
# Deliberately driven by the spec rather than a list maintained here: the spec
# is the authority on what this version needs, and `dnf builddep` reads it
# directly. That is the whole advantage of the RPM route over guessing package
# names, so hardcoding a list would give it up.
install_build_deps() {
    info "Installing build dependencies declared by the spec"
    if sudo dnf builddep -y "$SPEC"; then
        return 0
    fi

    warn "dnf builddep failed. This is usually an either/or dependency it"
    warn "cannot resolve automatically, such as (fdk-aac-devel or"
    warn "fdk-aac-free-devel). Trying the Fedora-native alternatives."

    # Best effort: --skip-unavailable so a package missing on this release
    # does not sink the whole transaction.
    sudo dnf install -y --skip-unavailable \
        dbus-glib-devel libXtst-devel libasan \
        libcbor-devel libfido2-devel libjpeg-turbo-devel libv4l-devel \
        libva-devel libwebp-devel pcsc-lite-devel uuid-devel || true

    # The either/or dependencies dnf cannot resolve on its own. Requested by
    # pkg-config capability where possible so whichever provider is already
    # installed satisfies it: Fedora ships patent-stripped -free variants
    # while RPM Fusion ships the full builds, and the two conflict. Forcing a
    # specific package name would swap out a user's existing multimedia stack
    # as a side effect of building an RDP client.
    if ! rpm -q --whatprovides "pkgconfig(libavcodec)" >/dev/null 2>&1; then
        sudo dnf install -y ffmpeg-devel 2>/dev/null \
            || sudo dnf install -y ffmpeg-free-devel 2>/dev/null \
            || warn "Could not install FFmpeg development headers."
    fi
    if ! rpm -q --whatprovides "pkgconfig(fdk-aac)" >/dev/null 2>&1; then
        sudo dnf install -y fdk-aac-free-devel 2>/dev/null \
            || sudo dnf install -y fdk-aac-devel 2>/dev/null \
            || warn "Could not install fdk-aac development headers."
    fi
}

install_build_deps

info "Building the RPM (this takes a while; the spec also runs the test suite)"

# Marks the moment the build started, so "freshly built" below means "newer
# than this run" rather than a guess at how long the build takes.
#
# Not a fixed age like -newermt '-2 hours': FreeRDP plus the spec's test suite
# exceeds two hours on modest hardware, which would exclude the RPMs this run
# just produced and report a successful build as a failure.
MARKER="$(mktemp -t entrardp-rpm-start.XXXXXX)"
trap 'rm -f "$MARKER"' EXIT

cd "$SRC"
bash "$CREATE" || die "create_rpm.sh failed. Its output above should say why."

# create_rpm.sh writes into the usual rpmbuild tree.
# `|| true` on the pipeline: grep -v exits 1 when every line is filtered out,
# and find exits non-zero if the tree does not exist. Either would abort here,
# losing the explicit empty-result message below that actually tells the user
# what to check.
mapfile -t RPMS < <(find "$HOME/rpmbuild/RPMS" -name 'freerdp-nightly-*.rpm' \
    -newer "$MARKER" 2>/dev/null | grep -v debuginfo | sort || true)

if [[ ${#RPMS[@]} -eq 0 ]]; then
    warn "No freshly built RPMs found under ~/rpmbuild/RPMS."
    warn "The build may have placed them elsewhere; check create_rpm.sh output."
    exit 1
fi

info "Built:"
printf '    %s\n' "${RPMS[@]}"

# -------------------------------------------------------------------- install

if [[ "$AUTO_INSTALL" == "1" ]]; then
    # The spec hardcodes version 3.0-0, so every rebuild has the same
    # name, version and release. `dnf install` treats that as already
    # installed and does nothing, leaving the previous binary in place
    # while reporting success. Rebuilding after an OS upgrade or for a
    # security release is the whole reason to run this again, so an
    # existing install is replaced explicitly.
    if rpm -q freerdp-nightly >/dev/null 2>&1; then
        info "Replacing the installed freerdp-nightly"
        sudo dnf reinstall -y "${RPMS[@]}"
    else
        info "Installing"
        sudo dnf install -y "${RPMS[@]}"
    fi

    # The nightly spec sets WITH_CLIENT_SDL_VERSIONED=ON, so the SDL client
    # installs as sdl-freerdp3 rather than sdl-freerdp. Distribution packages
    # commonly build it unversioned, so both names have to be handled.
    BIN=""
    for candidate in /opt/freerdp-nightly/bin/sdl-freerdp3 \
                     /opt/freerdp-nightly/bin/sdl-freerdp; do
        if [[ -x "$candidate" ]]; then
            BIN="$candidate"
            break
        fi
    done

    if [[ -n "$BIN" ]]; then
        report_signin "$BIN" || true
        info "Binary ready at $BIN"
    else
        warn "No SDL client found under /opt/freerdp-nightly/bin."
        warn "Contents:"
        # find rather than ls: ls output is not safe to parse, and shellcheck
        # flags it (SC2012). -printf keeps this to basenames.
        find /opt/freerdp-nightly/bin -maxdepth 1 -type f -printf '    %f\n' \
            2>/dev/null | sort || true
    fi
else
    info "AUTO_INSTALL=0; install manually with: sudo dnf install ${RPMS[*]}"
fi
