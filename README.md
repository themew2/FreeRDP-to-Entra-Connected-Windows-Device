# FreeRDP + Entra ID (AAD) Native Webview Auth on Fedora/Nobara

Building FreeRDP with native Entra ID (Azure AD) webview authentication support — the Linux equivalent of Windows' "Use a web account to sign in to the remote computer" checkbox in `mstsc.exe`.

The distro-packaged `freerdp` on most distros ships with the webview feature disabled, so you get a URL printed to the terminal instead of a native popup — requiring manual copy/paste of the redirect URL after signing in through an external browser. This guide covers building FreeRDP with `WITH_WEBVIEW=ON` to get the real, native popup experience, plus a couple of alternative install paths worth knowing about.

> **Note:** `WITH_WEBVIEW` has existed in FreeRDP since **3.16.0** — it's not a brand-new feature, just one most distro packagers leave disabled by default (likely to avoid the WebKitGTK dependency chain).

## Requirements

Clone FreeRDP and confirm the feature exists in your checkout before doing anything else:

    git clone https://github.com/FreeRDP/FreeRDP.git
    cd FreeRDP
    git log -1 --format="%H %ci"
    grep -r "WITH_WEBVIEW" client/SDL/common/aad/CMakeLists.txt

**What to look for:**
- The `grep` should return a match (`option(WITH_WEBVIEW ...)`). If it returns nothing, your checkout predates 3.16.0 — stay on `master` (don't check out an old release tag).
- This guide was built and tested against commit `220f9400e` (`FreeRDP version 3.30.1-dev0`).

You can spot-check an already-installed distro package the same way, to confirm whether it has webview enabled before deciding whether you need to build at all:

    xfreerdp /buildconfig | tr ' ' '\n' | grep -i webview

If this shows `WITH_WEBVIEW=OFF`, the steps below apply to you.

## Alternative install paths (worth checking before building manually)

Building from source with `ninja`/manual steps is one option, but it's not the only one:

- **RPM-based distros**: FreeRDP's own repo includes scripts to build a proper nightly RPM yourself, rather than compiling and running loose binaries out of a build directory. See `packaging/scripts/prepare_rpm_freerdp-nightly.sh` in the repo.
- **Flatpak**: a buildable Flatpak manifest also exists in the repo, if you'd prefer a sandboxed install over a system-wide one.
- **`WITH_SSO_MIB`**: if you already have a broker-style daemon installed locally (e.g., Microsoft's Linux Intune client, which provides `microsoft-identity-broker`-compatible D-Bus service), this build option lets FreeRDP retrieve SSO tokens automatically with no webview popup at all. Not covered in depth in this guide, but worth knowing if you already have that infrastructure in place — see [FreeRDP/FreeRDP#13201](https://github.com/FreeRDP/FreeRDP/issues/13201) for maintainer commentary.

This guide covers the straightforward manual build + `install` path below, which works regardless of distro.

## Step 1 — Install build dependencies

    sudo dnf install cmake ninja-build gcc-c++ git \
      systemd-devel libuuid-devel pulseaudio-libs-devel \
      libXrandr-devel gsm-devel pam-devel fuse3-devel \
      opus-devel lame-devel openssl-devel libX11-devel \
      libXext-devel libXinerama-devel libXcursor-devel \
      libXi-devel libXdamage-devel libXv-devel libxkbfile-devel \
      alsa-lib-devel openh264-devel libavcodec-free-devel \
      libavformat-free-devel libavutil-free-devel \
      libswresample-free-devel libswscale-free-devel \
      libusb1-devel uriparser-devel SDL2-devel SDL2_ttf-devel \
      pkcs11-helper-devel krb5-devel cjson-devel cairo-devel \
      soxr-devel wayland-devel wayland-protocols-devel \
      cups-devel webkitgtk6.0-devel

**Key package:** `webkitgtk6.0-devel` — the webview feature pulls in a small external helper library (`akallabeth/webview` via CMake FetchContent) which searches for WebKitGTK in this priority order: `webkitgtk-6.0` → `webkit2gtk-4.1` → `webkit2gtk-4.0`. Current Fedora has deprecated `webkit2gtk-4.0`, but `webkitgtk-6.0` (GTK4-based) works fine — no need to chase the deprecated package.

## Step 2 — Configure

    cd FreeRDP
    mkdir build && cd build
    cmake -GNinja -DWITH_WEBVIEW=ON -DWITH_CLIENT_SDL=ON -DWITH_CLIENT_SDL3=ON -DWITH_CLIENT_SDL2=OFF -DWITH_AAD=ON -DWITH_PULSE=ON ..

Confirm before building:

    grep -i "webview\|gtk4\|SDL3\|PULSE" CMakeCache.txt

> **Note on `WITH_PULSE`:** in testing on Fedora 44, this defaulted to `OFF` even with PulseAudio correctly detected on the system (all `PULSEAUDIO_*` paths resolved correctly in `CMakeCache.txt` — only the feature switch itself was off). This may not be universal across distros, so check your own `CMakeCache.txt` rather than assuming either way, and explicitly pass `-DWITH_PULSE=ON` regardless to be safe.

## Step 3 — Build and install properly

Don't just run binaries straight out of the build directory — build and install them properly so paths, plugin discovery, and future upgrades behave correctly:

    ninja
    sudo cmake --build . --target install

This installs the client, plugins, and libraries to their proper system locations rather than leaving you dependent on the raw build tree.

## Step 4 — Connect

    sdl-freerdp \
      /v:<remote-hostname> \
      /sec:aad \
      /azure:tenantid:<your-entra-tenant-id> \
      /u:<user>@<yourdomain>.com \
      /cert:ignore \
      /f \
      /w:2560 \
      /h:1440 \
      /clipboard \
      /microphone:sys:pulse \
      /sound:sys:pulse

(If you skipped the `install` step and are running from the build tree directly, the binary is at `./client/SDL/SDL3/sdl-freerdp` instead.)

**Flags that matter:**
- `/sec:aad` is required alongside `/azure:` — without it, the client falls back to NLA/Kerberos and fails with `Cannot find KDC for realm`.
- `<remote-hostname>` must match the Entra ID-registered device name exactly and must resolve via DNS/`/etc/hosts`.
- `/cert:ignore` skips TLS certificate verification for the connection. This is convenient for a lab/self-signed setup, but it means you won't be warned if a certificate doesn't match — don't use this against a machine over an untrusted network without understanding that tradeoff. Drop the flag (and properly trust the machine's certificate instead) for anything more sensitive than a home lab.
- Avoid combining `/smart-sizing` with `/f` (true fullscreen) — see Known Issues below.
- Minimize with **Right Shift + M**; toggle fullscreen with **Right Shift + Enter** (SDL client default keybinds, different from the older xfreerdp client).
- Adjust `/w` and `/h` to match your own display's resolution.

## Security note

FreeRDP had a critical (CVSS 9.8) heap use-after-free vulnerability in the cliprdr (clipboard) channel — **CVE-2026-25959** — affecting versions prior to **3.23.0**, fixed in that release. Since this guide tracks `master`, you're almost certainly well past the fix, but confirm your build's version before relying on it, especially if you've pinned an older commit for any reason:

    sdl-freerdp --version

If your version predates 3.23.0, update before using `/clipboard` in any untrusted environment.

## Known issues

- **`/smart-sizing` + fullscreen rendering artifacts (horizontal lines) on Wayland with the SDL3 client** — reported upstream as [FreeRDP/FreeRDP#13204](https://github.com/FreeRDP/FreeRDP/issues/13204), fixed via [PR #13205](https://github.com/FreeRDP/FreeRDP/pull/13205), targeted for the **3.31.0** release. If you're on a build after this merged, `/smart-sizing` should work correctly and this workaround is unnecessary. Until then: don't use `/smart-sizing`; use fixed `/w`/`/h` with `/f` instead.
- Distro-packaged FreeRDP builds typically ship with `WITH_WEBVIEW=OFF` by default (present since 3.16.0, just often disabled by packagers) — must be explicitly enabled and built to get the native popup.

## Using the wrapper script

See `rdp-aad.sh` in this repo for a ready-to-use wrapper with hostname resolution checking and sane defaults.

Edit the defaults at the top of `rdp-aad.sh` to match your environment:

    DEFAULT_HOST="your-vm-hostname"
    DEFAULT_USER="youruser@yourdomain.com"
    TENANT_ID="your-entra-tenant-id"
    FREERDP_BIN="sdl-freerdp"

(If you used `cmake --build . --target install` in Step 3, `sdl-freerdp` should already be on your `PATH`; if running from the raw build tree instead, set the full path: `$HOME/FreeRDP/build/client/SDL/SDL3/sdl-freerdp`.)

Then install it somewhere on your PATH and run it:

    mkdir -p ~/.local/bin
    cp rdp-aad.sh ~/.local/bin/rdp-aad
    chmod +x ~/.local/bin/rdp-aad
    rdp-aad

**Usage:**

    rdp-aad                       # connect to default host/user
    rdp-aad <hostname>            # connect to a specific host, default user
    rdp-aad <hostname> <user>     # connect to a specific host and user

## Making it a clickable application (KDE desktop launcher)

Rather than running `rdp-aad` from a terminal each time, create a `.desktop` entry so it shows up in your application menu/launcher like any other app.

Create `~/.local/share/applications/rdp-aad.desktop`:

    [Desktop Entry]
    Type=Application
    Name=RDP (Entra ID)
    Comment=Connect to Entra-joined Windows machine via FreeRDP SDL3 with AAD webview auth
    Exec=/home/YOUR_USERNAME/.local/bin/rdp-aad
    Icon=preferences-system-network
    Terminal=false
    Categories=Network;RemoteAccess;
    StartupNotify=true

Replace `/home/YOUR_USERNAME/` with your actual home directory path (or just use `Exec=rdp-aad` if `~/.local/bin` is already on your `PATH`).

Refresh KDE's application cache so it appears immediately:

    kbuildsycoca6

It should now show up in your application launcher (search "RDP" in KRunner or your app menu) as **"RDP (Entra ID)"**, launching without a visible terminal window.

**Note:** `Terminal=false` means any of the script's own console output (like the hostname-resolution warning) won't be visible if something goes wrong — for troubleshooting, run `rdp-aad` directly from a terminal instead.

## License

Released under [The Unlicense](LICENSE) — public domain, no conditions, use it however you'd like.

## Acknowledgments

Thanks to [@akallabeth](https://github.com/akallabeth) on the FreeRDP team for reviewing this work, correcting several details in this guide, and confirming/fixing the `/smart-sizing` rendering bug documented above. See the discussion on [FreeRDP/FreeRDP#13201](https://github.com/FreeRDP/FreeRDP/issues/13201).
