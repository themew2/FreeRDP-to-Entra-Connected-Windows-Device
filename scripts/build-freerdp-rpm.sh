#!/usr/bin/env bash
#
# Builds an RPM of FreeRDP with WITH_WEBVIEW=ON using upstream's own packaging.
#
# This is the route recommended by the FreeRDP maintainers for RPM-based
# distributions (FreeRDP#13237): modify packaging/rpm/freerdp-nightly.spec to
# enable webview, then build with packaging/scripts/create_rpm.sh.
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

SRC="${ENTRARDP_RPM_SRC:-$HOME/.cache/entrardp/FreeRDP-rpm}"
# Set by resolve_version(): the newest release tag, or FREERDP_BRANCH if set.
BRANCH=""
# Defaults on: debug instrumentation is for diagnosing FreeRDP itself, not
# for a client someone takes calls on.
RELEASE_BUILD="${RELEASE_BUILD:-1}"
AUTO_INSTALL="${AUTO_INSTALL:-1}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "Do not run this as root."
command -v rpmbuild >/dev/null || command -v dnf >/dev/null || \
    die "This script requires an RPM-based distribution. Use scripts/build-freerdp.sh instead."


# ------------------------------------------------------- version resolution

# Resolve the newest stable release tag rather than defaulting to a branch.
#
# FreeRDP tags releases directly on master (3.31.1, 3.31.0, ...). Defaulting
# to a branch name is fragile: master carries unreleased changes, and a
# stable-x.y branch may not track the current series. Resolving the newest tag
# at build time always yields a real release, without hardcoding a version
# that goes stale. Security releases are frequent here, so being a few
# versions behind is a real exposure rather than a cosmetic issue.
resolve_version() {
    if [[ -n "${FREERDP_BRANCH:-}" ]]; then
        BRANCH="$FREERDP_BRANCH"
        info "Using requested ref: $BRANCH"
        return 0
    fi

    info "Resolving the newest FreeRDP release tag"
    local tags
    tags=$(git ls-remote --tags --refs https://github.com/FreeRDP/FreeRDP.git 2>/dev/null \
        | sed 's#.*refs/tags/##' \
        | grep -E '^3\.[0-9]+\.[0-9]+$' \
        | sort -t. -k1,1n -k2,2n -k3,3n) || true

    if [[ -z "$tags" ]]; then
        warn "Could not list remote tags. Set FREERDP_BRANCH to a release, e.g. 3.31.1"
        die "Unable to determine which version to build."
    fi

    BRANCH=$(echo "$tags" | tail -1)

    # Webview support arrived in 3.16.0.
    local minor
    minor=$(echo "$BRANCH" | cut -d. -f2)
    if [[ "$minor" -lt 16 ]]; then
        die "Newest tag $BRANCH predates webview support (needs 3.16.0 or newer)."
    fi

    info "Building release $BRANCH"
}

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

# The spec already declares the webkit BuildRequires for Fedora and RHEL:
#   BuildRequires: (webkitgtk6.0-devel or webkit2gtk4.1-devel or webkit2gtk4.0-devel)
# so enabling the feature is a single-line change.
# The cmake arguments are indented inside the %build section, so anchor on
# optional leading whitespace rather than the start of the line. Using -- and
# no backslash before the hyphen: escaping it makes GNU grep warn about a
# stray backslash, and the leading [[:space:]]* already prevents the pattern
# being read as an option.
if grep -qE '^[[:space:]]*-DWITH_WEBVIEW=OFF' "$SPEC"; then
    info "Enabling WITH_WEBVIEW in the spec"
    sed -i -E 's/^([[:space:]]*)-DWITH_WEBVIEW=OFF/\1-DWITH_WEBVIEW=ON/' "$SPEC"
elif grep -qE '^[[:space:]]*-DWITH_WEBVIEW=ON' "$SPEC"; then
    info "Spec already has WITH_WEBVIEW=ON"
else
    die "No WITH_WEBVIEW line found in the spec. Inspect $SPEC and set it by hand."
fi

if [[ "$RELEASE_BUILD" == "1" ]]; then
    info "Switching to a Release build without debug instrumentation"
    sed -i -E 's/^([[:space:]]*)-DCMAKE_BUILD_TYPE=Debug/\1-DCMAKE_BUILD_TYPE=Release/' "$SPEC"
    sed -i -E 's/^([[:space:]]*)-DWITH_SANITIZE_ADDRESS=ON/\1-DWITH_SANITIZE_ADDRESS=OFF/' "$SPEC"
    sed -i -E 's/^([[:space:]]*)-DCMAKE_C_FLAGS="-O1"/\1-DCMAKE_C_FLAGS="-O2"/' "$SPEC"
    sed -i -E 's/^([[:space:]]*)-DCMAKE_CXX_FLAGS="-O1"/\1-DCMAKE_CXX_FLAGS="-O2"/' "$SPEC"

    # These two are FreeRDP cmake defaults rather than spec settings, so they
    # survive the build type change and have to be turned off explicitly.
    # The client announces both on every connection:
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
    # a client, so turning it off costs nothing.
    #
    # Inserted after the webview line rather than matched in place: neither
    # appears in the spec, because both are cmake defaults.
    if grep -qE '^[[:space:]]*-DWITH_VERBOSE_WINPR_ASSERT' "$SPEC"; then
        info "Spec already sets WITH_VERBOSE_WINPR_ASSERT"
    else
        sed -i '/-DWITH_WEBVIEW=ON/a\    -DWITH_VERBOSE_WINPR_ASSERT=OFF \\\n    -DWITH_VAAPI_H264_ENCODING=OFF \\' "$SPEC"
    fi
else
    warn "RELEASE_BUILD=0: building upstream's nightly test configuration."
    warn "Expect reduced performance and degraded audio. Use this only when"
    warn "reproducing a bug for the FreeRDP project."
fi

info "Resulting build flags:"
# `|| true` because this only reports state. grep exits 1 when it matches
# nothing, and under `set -euo pipefail` that would abort the build from a
# line whose only job is to print a summary.
grep -E '^[[:space:]]*-D(WITH_WEBVIEW|CMAKE_BUILD_TYPE|WITH_SANITIZE_ADDRESS|WITH_VERBOSE_WINPR_ASSERT|WITH_VAAPI_H264_ENCODING)' "$SPEC" || true

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
cd "$SRC"
bash "$CREATE" || die "create_rpm.sh failed. Its output above should say why."

# create_rpm.sh writes into the usual rpmbuild tree.
# `|| true` on the pipeline: grep -v exits 1 when every line is filtered out,
# and find exits non-zero if the tree does not exist. Either would abort here,
# losing the explicit empty-result message below that actually tells the user
# what to check.
mapfile -t RPMS < <(find "$HOME/rpmbuild/RPMS" -name 'freerdp-nightly-*.rpm' \
    -newermt '-2 hours' 2>/dev/null | grep -v debuginfo | sort || true)

if [[ ${#RPMS[@]} -eq 0 ]]; then
    warn "No freshly built RPMs found under ~/rpmbuild/RPMS."
    warn "The build may have placed them elsewhere; check create_rpm.sh output."
    exit 1
fi

info "Built:"
printf '    %s\n' "${RPMS[@]}"

# -------------------------------------------------------------------- install

if [[ "$AUTO_INSTALL" == "1" ]]; then
    info "Installing"
    sudo dnf install -y "${RPMS[@]}"

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
        # Captured with command substitution rather than piped into grep -q.
        # Under `set -o pipefail`, grep -q exits on the first match and closes
        # the pipe; the upstream command takes SIGPIPE writing the rest of its
        # output and the pipeline reports 141, turning a good build into an
        # apparent failure. /buildconfig output is long enough that this is the
        # likely case rather than the edge case.
        #
        # `|| true` because /buildconfig is not guaranteed to exit 0.
        local_cfg="$("$BIN" /buildconfig 2>&1 || true)"
        if grep -qi '^WITH_WEBVIEW=ON$' <<<"$(tr ' ' '\n' <<<"$local_cfg")"; then
            info "Verified: WITH_WEBVIEW=ON"
        else
            warn "Installed, but could not confirm webview support."
        fi
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
