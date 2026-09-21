# shellcheck shell=bash
#
# Helpers shared by install.sh, build-freerdp.sh and build-freerdp-rpm.sh.
# Sourced, never executed.
#
# Source this before the first use of info/warn/die, which in practice means
# immediately after setting HERE.

# ---------------------------------------------------------------- reporting

# Progress, non-fatal problem, and fatal problem respectively. Colour goes to
# whichever stream the message belongs on: info to stdout, the other two to
# stderr, so redirecting one does not lose the others.
info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------ version resolution

# Resolve the version to build into $BRANCH.
#
# FreeRDP tags releases directly on master (3.31.1, 3.31.0, ...), so defaulting
# to a branch name is fragile: master carries unreleased changes, and a
# stable-x.y branch may not track the current series. Resolving the newest tag
# at build time always yields a real release without hardcoding a version that
# goes stale. Security releases here are frequent and substantial, so trailing
# the current release by a few versions is a real exposure, not a cosmetic one.
#
# Shared rather than duplicated because the two build scripts must agree on
# what they produce: the README, the RPM script's header, and freerdp.py's
# path-ranking comments all describe both routes as building the same release.
#
# $FREERDP_BRANCH overrides, to pin a version or test unreleased changes.
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
