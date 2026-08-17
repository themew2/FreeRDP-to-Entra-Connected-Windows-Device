# FreeRDP + Entra ID (AAD) Native Webview Auth on Fedora/Nobara

Building FreeRDP from source with native Entra ID (Azure AD) webview authentication support — the Linux equivalent of Windows' "Use a web account to sign in to the remote computer" checkbox in `mstsc.exe`.

The distro-packaged `freerdp` ships without the native in-app login popup — you get a URL printed to the terminal instead, requiring manual copy/paste of the redirect URL after signing in through an external browser. This guide builds FreeRDP with `WITH_WEBVIEW=ON` to get the real, native popup experience.

## Requirements

Before doing anything else, clone FreeRDP and check what you actually have — this guide requires a recent build with `WITH_WEBVIEW` and AAD support, which are not present in older release tags.

    git clone https://github.com/FreeRDP/FreeRDP.git
    cd FreeRDP
    git log -1 --format="%H %ci"
    grep -r "WITH_WEBVIEW" client/SDL/common/aad/CMakeLists.txt

**What to look for:**
- The `grep` command should return a match (`option(WITH_WEBVIEW ...)`). If it returns nothing, you've checked out a version that predates this feature entirely — stay on the `master` branch (don't check out a release tag) and pull the latest commits instead.
- This guide was built and tested against commit `220f9400e` (reported as `FreeRDP version 3.30.1-dev0` via `--version` once built). Anything reasonably close to this on `master` should work; older tagged releases (3.0.x-3.x early releases) will not.

Once confirmed, continue to the dependency install below.

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

- **FreeRDP master/dev branch** — this was built and tested against **FreeRDP 3.30.1-dev0** (commit `220f9400e`). The `WITH_WEBVIEW` build option and the `/azure:` AAD flag are relatively recent additions, so an older release tag (anything pre-3.x, or early 3.x releases) may not have these features at all.
- Confirm your cloned version supports what you need before building:

      cd FreeRDP
      git log -1 --oneline
      grep -r "WITH_WEBVIEW" client/SDL/common/aad/CMakeLists.txt

  If that grep returns nothing, you've cloned a version that predates this feature — pull the latest `master` branch instead of a specific release tag.

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
- Setting resolution will depend on your local resolution capabilities. 

## Known issues

- SDL3 client + Wayland: horizontal line rendering artifacts when combining `/smart-sizing` with fullscreen mode. Workaround: don't use `/smart-sizing`; use fixed `/w`/`/h` with `/f` instead.
- Distro-packaged FreeRDP builds typically ship with `WITH_WEBVIEW=OFF` and `WITH_PULSE=OFF` by default — both must be explicitly enabled and built from source.

## Wrapper script

See `rdp-aad.sh` in this repo for a ready-to-use wrapper with hostname resolution checking and sane defaults.

## Using the wrapper script

Edit the defaults at the top of `rdp-aad.sh` to match your environment:

    DEFAULT_HOST="your-vm-hostname"
    DEFAULT_USER="youruser@yourdomain.com"
    TENANT_ID="your-entra-tenant-id"
    FREERDP_BIN="$HOME/FreeRDP/build/client/SDL/SDL3/sdl-freerdp"

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

Rather than running `rdp-aad` from a terminal each time, you can create a `.desktop` entry so it shows up in your application menu/launcher like any other app.

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
