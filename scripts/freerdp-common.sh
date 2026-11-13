# shellcheck shell=bash
#
# Helpers shared by install.sh and build-freerdp.sh.
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
        # No version named here: a hardcoded example goes stale, and the one
        # thing worse than this failure is someone pasting an old tag from it
        # and building a client with known advisories against it.
        warn "Could not list FreeRDP release tags, usually no network route to github.com."
        warn "Pick a release 3.16.0 or newer from"
        warn "    https://github.com/FreeRDP/FreeRDP/releases"
        warn "and pin it, for example"
        warn "    FREERDP_BRANCH=<tag> $0"
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

# ------------------------------------------------------- sign-in mechanism

# Set SIGNIN_ARGS (cmake -D arguments) and SIGNIN_HELPERS (helper short names)
# for the FreeRDP source tree in $1.
#
# FreeRDP 3.32.0 replaced the in-process WITH_WEBVIEW with out-of-process
# sign-in helpers, each behind its own option, all defaulting OFF:
#
#   WITH_XDG_AAD_AUTH_HELPER      signs in through the default web browser
#   WITH_WEBVIEW_AAD_AUTH_HELPER  embedded WebKitGTK window, the old look
#
# Passing -DWITH_WEBVIEW=ON to a 3.32+ tree is silently ignored and produces a
# client with no sign-in window at all. Both helpers are built: the client's
# own autodetection prefers the browser, and the app lets either be chosen.
#
# Decided from the tree rather than the version string, so a branch or an
# arbitrary commit is handled as correctly as a release tag.
# shellcheck disable=SC2034  # SIGNIN_* are read by the scripts sourcing this
resolve_signin_flags() {
    local src=$1
    if [[ -d "$src/client/common/webview-aad-helper" ]]; then
        SIGNIN_HELPERS=(xdg webview)
        SIGNIN_ARGS=(-DWITH_XDG_AAD_AUTH_HELPER=ON -DWITH_WEBVIEW_AAD_AUTH_HELPER=ON)
        info "Sign-in: out-of-process helpers (FreeRDP 3.32+): ${SIGNIN_HELPERS[*]}"
    else
        SIGNIN_HELPERS=()
        SIGNIN_ARGS=(-DWITH_WEBVIEW=ON)
        info "Sign-in: in-process webview (FreeRDP before 3.32)"
    fi
}

# Report whether the installed client at $1 can sign in, and return non-zero
# if it cannot. Helpers are looked for where the client looks: beside it and
# in ../libexec.
#
# Output is captured with command substitution rather than piped into grep -q:
# under pipefail, grep -q exits on the first match, the writer takes SIGPIPE,
# and the pipeline reports 141 for a successful match.
report_signin() {
    local bin=$1 dir present=() name cfg
    dir="$(cd "$(dirname "$bin")" && pwd)"
    for name in xdg qt webview; do
        if [[ -x "$dir/freerdp-$name-aad-helper" || -x "$dir/../libexec/freerdp-$name-aad-helper" ]]; then
            present+=("$name")
        fi
    done
    if [[ ${#present[@]} -gt 0 ]]; then
        info "Verified: sign-in helpers installed: ${present[*]}"
        return 0
    fi
    # `|| true`: /buildconfig is not guaranteed to exit 0.
    cfg="$("$bin" /buildconfig 2>&1 || true)"
    # Whitespace stripped so the match survives "WITH_WEBVIEW = ON" spacing and
    # the -D prefix alike. WITH_WEBVIEW_AAD_AUTH_HELPER=ON cannot match it.
    if [[ "${cfg//[[:space:]]/}" == *"WITH_WEBVIEW=ON"* ]]; then
        info "Verified: WITH_WEBVIEW=ON (in-process sign-in window)"
        return 0
    fi
    warn "No sign-in helper or webview found for $bin."
    warn "Sign-in will fall back to printing a URL to copy and paste back."
    return 1
}
