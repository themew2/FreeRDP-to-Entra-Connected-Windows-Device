# FreeRDP + Entra ID (AAD) Native Webview Auth on Fedora/Nobara

Building FreeRDP from source with native Entra ID (Azure AD) webview authentication support — the Linux equivalent of Windows' "Use a web account to sign in to the remote computer" checkbox in `mstsc.exe`.

The distro-packaged `freerdp` ships without the native in-app login popup — you get a URL printed to the terminal instead, requiring manual copy/paste of the redirect URL after signing in through an external browser. This guide builds FreeRDP with `WITH_WEBVIEW=ON` to get the real, native popup experience.

## The problem with the stock package

    xfreerdp /buildconfig | tr ' ' '\n' | grep -i webview

shows `WITH_WEBVIEW=OFF` on most distro packages.

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

**Key package:** `webkitgtk6.0-devel` — FreeRDP's webview feature pulls in a small external helper library (`akallabeth/webview` via CMake FetchContent) which searches for WebKitGTK in this priority order: `webkitgtk-6.0` → `webkit2gtk-4.1` → `webkit2gtk-4.0`. Current Fedora has deprecated `webkit2gtk-4.0`, but `webkitgtk-6.0` (GTK4-based) works fine.

## Step 2 — Clone and configure (SDL3 client, webview, AAD, and PulseAudio all enabled)

    git clone https://github.com/FreeRDP/FreeRDP.git
    cd FreeRDP
    mkdir build && cd build
    cmake -GNinja -DWITH_WEBVIEW=ON -DWITH_CLIENT_SDL=ON -DWITH_CLIENT_SDL3=ON -DWITH_CLIENT_SDL2=OFF -DWITH_AAD=ON -DWITH_PULSE=ON ..

Confirm before building:

    grep -i "webview\|gtk4\|SDL3\|PULSE" CMakeCache.txt

## Step 3 — Build

    ninja

## Step 4 — Find the binary

    find . -iname "*freerdp*" -executable -type f

Lands at `./client/SDL/SDL3/sdl-freerdp`.

## Step 5 — Connect

    ./client/SDL/SDL3/sdl-freerdp \
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

**Flags that matter:**
- `/sec:aad` is required alongside `/azure:` — without it, the client falls back to NLA/Kerberos and fails with `Cannot find KDC for realm`.
- `<remote-hostname>` must match the Entra ID-registered device name exactly and must resolve via DNS/`/etc/hosts`.
- Avoid combining `/smart-sizing` with `/f` (true fullscreen) — this combination has known rendering issues (horizontal line artifacts) on Wayland with the SDL3 client. Use `/f` with fixed `/w`/`/h` instead.
- Minimize with **Right Shift + M**; toggle fullscreen with **Right Shift + Enter** (SDL client default keybinds, different from the older xfreerdp client).

## Known issues

- SDL3 client + Wayland: horizontal line rendering artifacts when combining `/smart-sizing` with fullscreen mode. Workaround: don't use `/smart-sizing`; use fixed `/w`/`/h` with `/f` instead.
- Distro-packaged FreeRDP builds typically ship with `WITH_WEBVIEW=OFF` and `WITH_PULSE=OFF` by default — both must be explicitly enabled and built from source.

## Wrapper script

See `rdp-aad.sh` in this repo for a ready-to-use wrapper with hostname resolution checking and sane defaults.
