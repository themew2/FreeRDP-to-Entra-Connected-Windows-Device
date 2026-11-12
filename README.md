# Entra RDP

A desktop app for connecting to **Microsoft Entra ID (Azure AD) joined Windows machines** from Linux, with native web account sign-in — the equivalent of the *"Use a web account to sign in to the remote computer"* checkbox in Windows' `mstsc.exe`.

**Azure Virtual Desktop** is supported too, by pointing the app at a workspace file downloaded from the AVD web client. See [Azure Virtual Desktop](#azure-virtual-desktop).

![Entra RDP](data/screenshots/main-window.png)

---

## The problem this solves

Signing in to an Entra-joined machine needs a browser. How FreeRDP provides one depends on its version and on how it was built, and two binaries with the same name can behave completely differently:

| FreeRDP | Sign-in window | Enabled by |
|---|---|---|
| before 3.16 | none | — |
| 3.16 – 3.31 | embedded WebKitGTK window inside the client | `WITH_WEBVIEW` |
| 3.32 and newer | separate **sign-in helper** programs: the default web browser, or an embedded window | `WITH_XDG_AAD_AUTH_HELPER`, `WITH_WEBVIEW_AAD_AUTH_HELPER`, `WITH_QT_AAD_AUTH_HELPER` |

A build with none of these degrades to a copy-and-paste ritual: the client prints a login URL, you open it in a browser, sign in, then paste the redirect URL back into the terminal.

For years distribution packages shipped that way, because the in-process webview pulls its helper library through CMake FetchContent at configure time, which packaging policies forbid. FreeRDP 3.32 moved sign-in out of the client into helpers, and the browser-based one needs nothing fetched. **Fedora 45 ships it:**

```console
$ /usr/bin/sdl-freerdp /buildconfig | tr ' ' '\n' | grep -E 'WITH_AAD|AUTH_HELPER'
WITH_AAD=ON
WITH_QT_AAD_AUTH_HELPER=OFF
WITH_WEBVIEW_AAD_AUTH_HELPER=OFF
WITH_XDG_AAD_AUTH_HELPER=ON
```

So on a current distribution there may be nothing to compile at all. This app probes whichever binary it is about to run and tells you which sign-in methods it offers — or that it has none, or that it can no longer start — before you connect. **Name equality is not build equality.** To see the same verdict from a terminal:

```bash
PYTHONPATH=src python3 -m entrardp.freerdp            # every installed client
PYTHONPATH=src python3 -m entrardp.freerdp /path/to/sdl-freerdp
```

---

## Platform support

| Platform | Status |
|---|---|
| **Fedora 45** | Tested. The distribution's FreeRDP signs in through the default browser; no compile |
| **Fedora 44** | Tested end to end through both build routes |
| Other Fedora / RHEL | Should work. Uses the distribution package if it has a sign-in helper, otherwise builds from upstream's own spec |
| Debian / Ubuntu / Arch | **Untested.** Uses the distribution package if it qualifies, otherwise the source build, whose dependency lists have only been exercised on Fedora |
| Anything else | No automatic FreeRDP build. Supply your own binary and use `SKIP_FREERDP=1` |

The GUI itself is distribution-independent — it needs a FreeRDP binary that can sign in and does not care where that came from.

On untested platforms the source build runs a preflight check through `pkg-config` before compiling, so a wrong package name produces a precise list of what is missing rather than an obscure build failure. Reports of what was actually needed on your distribution are welcome.

## Install

```bash
git clone https://github.com/themew2/FreeRDP-to-Entra-Connected-Windows-Device.git
cd FreeRDP-to-Entra-Connected-Windows-Device
./scripts/install.sh
```

One command. It installs PyQt6 if needed, checks every installed FreeRDP client for sign-in support, compiles one **only if none qualifies**, then installs the app and its desktop entry. On Fedora 45 that takes seconds. When a compile is needed, budget 20–40 minutes. Only package installation needs `sudo`.

When it does compile, it builds the newest FreeRDP release with **both** sign-in helpers — default browser and embedded window — so the app can offer either:

- **With `dnf`** it builds through FreeRDP's own packaging, producing a package `dnf` tracks, installed to `/opt/freerdp-nightly`. This is the route the FreeRDP maintainers suggested ([FreeRDP#13237](https://github.com/FreeRDP/FreeRDP/issues/13237)).
- **Elsewhere** it compiles into a private prefix under `~/.local/share/entrardp`. Its dependency lists are maintained here rather than upstream and have only been exercised on Fedora — see [Platform support](#platform-support).

| Variable | Effect |
|---|---|
| `FORCE_FREERDP_BUILD=1` | Compile even though an installed FreeRDP can sign in. The way to get the embedded sign-in window when your distribution only ships the browser helper |
| `SKIP_FREERDP=1` | Never compile. Select your binary in the app's *FreeRDP binary* field, which reports what it supports |

The app itself is copied to `~/.local/share/entrardp/app` and started by a small launcher at `~/.local/bin/entrardp`, which runs it with whichever `python3` is current. Earlier versions used `pip install --user`, which ties an install to one Python version and broke when Fedora 45 moved from Python 3.14 to 3.15; re-running `install.sh` removes that old copy.

## What the scripts do

`install.sh` is the entry point. The two build scripts are the routes it may take, and can be run directly.

```
install.sh
  ├─ python3-pyqt6                installed if missing
  ├─ python3 -m entrardp.freerdp  can an installed FreeRDP sign in already?
  │    yes → no compile
  │    no  → build-freerdp-rpm.sh   with dnf
  │          build-freerdp.sh       everywhere else
  ├─ the app, to ~/.local/share/entrardp/app, launcher ~/.local/bin/entrardp
  └─ desktop entry, icons, AppStream metainfo
```

Both build scripts decide what to enable **from the FreeRDP source tree itself**: a tree with `client/common/webview-aad-helper` (3.32+) gets the browser and embedded-window helpers, an older one gets `WITH_WEBVIEW`. Passing `-DWITH_WEBVIEW=ON` to a 3.32 tree is silently ignored by CMake and yields a client with no sign-in window, which is what this project's own scripts did until they learned the difference.

### `scripts/build-freerdp-rpm.sh` — dnf systems

Builds an RPM using FreeRDP's own `packaging/rpm/freerdp-nightly.spec` and `packaging/scripts/create_rpm.sh`, patching the spec to enable both sign-in helpers. The spec already declares the WebKitGTK build dependency and packages everything installed to `libexec`, where the helpers go.

What it gains you over the source route:

- `dnf` owns the result, so `dnf remove freerdp-nightly` uninstalls cleanly
- build dependencies come from the spec via `dnf builddep`, not a list maintained here
- installs to `/opt/freerdp-nightly`, coexisting with your distribution's FreeRDP
- the binary is named `sdl-freerdp3`, since the nightly spec sets `WITH_CLIENT_SDL_VERSIONED=ON`

**The "nightly" in the file name refers to how upstream publishes test packages, not to the branch built.** The source is the same tagged release the other script uses. What differs is the spec's build configuration, which is tuned for nightly testing: debug build, AddressSanitizer, `WITH_VERBOSE_WINPR_ASSERT`, experimental VAAPI H264 encoding. This script turns all of that off by default, because a build carrying it produced audibly degraded microphone capture in Teams calls where a plain release build of the same tag did not.

The spec hardcodes version `3.0-0`, so every rebuild looks identical to `dnf`, and `dnf install` would report success while leaving the old binary in place. The script uses `dnf reinstall` when the package is already installed.

| Variable | Default | Purpose |
|---|---|---|
| `RELEASE_BUILD=0` | on | Build upstream's nightly test configuration instead. Slower, and known to degrade audio. Use only when reporting a bug to FreeRDP |
| `AUTO_INSTALL=0` | on | Build the RPM but do not install it |
| `FREERDP_BRANCH` | newest release tag | Branch or tag to build. Must be ≥ 3.16.0 for any sign-in window |

### `scripts/build-freerdp.sh` — everywhere else

Compiles FreeRDP directly into `~/.local/share/entrardp/freerdp`, a private prefix that will not collide with your distribution's package. Used on systems without `dnf`, and usable anywhere if you would rather not involve the package manager.

| Phase | What happens |
|---|---|
| Dependencies | Detects `dnf` / `apt` / `pacman` and installs the toolchain and headers |
| Preflight | Verifies every prerequisite with `pkg-config` and reports *all* missing ones at once |
| Resolve version | Looks up the newest `3.x.y` release tag from the remote, and refuses anything below 3.16.0 |
| Configure | From scratch every time, with the sign-in flags for this tree, `-DWITH_AAD=ON -DWITH_PULSE=ON`, and `WITH_SSO_MIB` auto-detected |
| Gate | Aborts unless each sign-in helper's directory was actually configured |
| Build & install | `cmake --build`, then `--target install` |
| Verify | Confirms the helpers were installed beside the client |

The gate exists because the failure it prevents is a *silent* one: CMake accepts a flag nothing defines without complaint, so a build can succeed, install cleanly, run fine, and simply lack the feature you asked for. Checking the CMake cache is not enough — an unknown flag is cached too — so the gate checks that CMake configured the helper's own subdirectory.

Configuring from scratch matters after an OS upgrade, which is the usual reason to rebuild: a cache from before it records library versions that no longer exist.

| Variable | Default | Purpose |
|---|---|---|
| `SKIP_DEPS=1` | off | Skip package installation; preflight still runs |
| `SKIP_PREFLIGHT=1` | off | Continue despite preflight warnings, when a library is present under an unexpected pkg-config name |
| `FREERDP_BRANCH` | newest release tag | Branch or tag to build |
| `ENTRARDP_PREFIX` | `~/.local/share/entrardp/freerdp` | Install somewhere else |
| `JOBS` | all cores | Limit parallel compilation |
| `WITH_SSO_MIB` | `auto` | Automatic token retrieval via a local identity broker |

### `scripts/diagnose-icon.sh`

Reports why the application icon may not be appearing, checking each step of the desktop lookup chain.

### Uninstalling

```bash
rm -rf ~/.local/share/entrardp/app ~/.local/bin/entrardp
rm -f ~/.local/share/applications/io.github.themew2.EntraRDP.desktop
rm -rf ~/.config/entrardp ~/.cache/entrardp
```

Then remove FreeRDP, if this project built one:

```bash
sudo dnf remove freerdp-nightly        # RPM route
rm -rf ~/.local/share/entrardp         # source route
```

---

## Usage

Fill in the fields and press Connect:

| Field | Notes |
|---|---|
| **Host name** | Must match the Entra-registered device name **exactly**, and must resolve via DNS or `/etc/hosts`. |
| **AVD workspace** | Optional. A `.rdpw` file from the Azure Virtual Desktop web client. Replaces the host name — see below. |
| **User name** | `you@yourdomain.com` |
| **Tenant ID** | Your Entra tenant GUID, from Azure Portal → Microsoft Entra ID → Overview. |

Either a host name or a workspace file identifies the target; one of the two is required, and setting both is reported as a conflict.

Save the combination as a named profile to reuse it. The app reopens on whichever profile you last saved or loaded.

Profiles live in `~/.config/entrardp/profiles.json` and the last-used name in `~/.config/entrardp/state.json`, both mode `600`.

**No credentials are ever stored.** Authentication happens entirely in the sign-in window, which belongs to FreeRDP, not to this app. The app keeps only hostnames, usernames, tenant IDs, and the path to a workspace file.

### Sign-in method

The *FreeRDP binary* section reports how the selected binary signs in, in plain terms:

| Report | Meaning |
|---|---|
| **Sign-in helper available** (green) | FreeRDP 3.32+ with at least one helper installed beside it. No copy and paste |
| **Embedded sign-in window** (green) | FreeRDP 3.16–3.31 built with `WITH_WEBVIEW` |
| **No sign-in window** (orange) | Sign-in will print a URL to paste into a browser and back. Also says when a helper was configured but its program is missing |
| **This build cannot start** (red) | Libraries it was linked against are gone, almost always after an OS upgrade. Connect is disabled, and the app switches to a working binary if it can find one |

When the binary has more than one helper, *Sign-in method* chooses between them:

| Choice | What happens |
|---|---|
| **Automatic** | FreeRDP picks, in its own order: default browser, then Qt, then WebKitGTK |
| **Default web browser** | Opens the sign-in page in your browser, which already holds any Microsoft session you have. FreeRDP registers a small handler to receive the result |
| **Embedded window (WebKitGTK / Qt WebEngine)** | A dedicated sign-in window, the pre-3.32 experience |

The choice becomes `/azure:…,auth-helper:<path>` in the command preview, and is saved with the profile by name rather than path, so a profile moves cleanly between machines and FreeRDP installs. A profile asking for a helper the current binary lacks falls back to Automatic without forgetting the preference.

### On-screen toolbar

A fullscreen session covers the whole screen, which on a single monitor leaves no visible way out. Tick **On-screen toolbar in fullscreen** to get a small bar at the top edge with minimize, restore and close buttons.

FreeRDP's SDL client, which the app normally runs, has no such toolbar: it accepts `/floatbar` and ignores it. So with the box ticked the app runs **`xfreerdp`, FreeRDP's X11 client, from the same install** instead, adding:

```
/floatbar:sticky:off,default:visible,show:fullscreen
```

The bar starts visible, slides away, and comes back when the mouse touches the top edge of the screen. It appears only in fullscreen. Sign-in is unchanged — both clients use the same sign-in helpers — and every session option applies to either client. The checkbox is greyed out when no `xfreerdp` (or `xfreerdp3`) sits beside the selected binary; Fedora's `freerdp` package and both build routes here install one. The setting is saved with the profile.

### Azure Virtual Desktop

AVD does not hand out a host name. The web client at [client.wvd.microsoft.com](https://client.wvd.microsoft.com/arm/webclient/) gives you a **workspace file** instead — a `.rdpw` carrying the session host, the gateway address, and a load balancing token.

Download one, then select it with **Browse…** next to *AVD workspace*. FreeRDP 3 parses `.rdpw` natively, so the file is passed through untouched — nothing in this app reads or rewrites it.

Selecting a file changes three things:

- The **host name** field greys out and its value is ignored. It is disabled rather than cleared, so pressing **Clear** restores your previous setup intact.
- `/v:` is omitted from the command, and the file is passed positionally alongside `/gateway:type:arm`. Without that flag the client tries to reach the session host directly and fails.
- The DNS check is skipped, since there is no host name to resolve.

**Workspace files expire.** The certificate inside one is valid for a few months after download, and there is no API to refresh it — the only remedy is downloading a new file from the web client. The app warns once a file is more than 90 days old, using its modification time as a stand-in for the download date. That is an approximation, so it warns rather than blocks, and a connection failing with `0x1608` after the warning is almost always this.

The field is saved with the profile, and profiles written before this feature existed load unchanged.

> **Untested against a live AVD deployment.** The flag handling follows FreeRDP's documented behaviour for `.rdpw` input, but nobody has yet confirmed it end to end. Reports either way are welcome.

### Session options

Every checkbox maps to exactly one FreeRDP flag, shown in its tooltip and reflected live in the command preview. Nothing is hidden — if the app misbehaves, copy the previewed command and run it in a terminal to see the raw output.

Defaults match a verified-working configuration: fullscreen, fixed resolution, certificate bypass, audio in and out, and clipboard sharing.

### Environment

Two checkboxes and a free-text list build the `env` prefix shown at the front of the command preview:

| Control | Effect |
|---|---|
| **Force X11 video driver** | `SDL_VIDEODRIVER=x11`. On by default; an embedded sign-in window does not map reliably on native Wayland. Unneeded when signing in through the browser. |
| **Disable WebKit compositing** | `WEBKIT_DISABLE_COMPOSITING_MODE=1`. Off by default. Turn it on if an embedded WebKitGTK sign-in window crashes. Sign-in helpers inherit the client's environment, so this reaches them too. |
| **Custom variables** | One `KEY=VALUE` per line. Values may contain spaces, and a surrounding pair of quotes is dropped so a line copied from a shell snippet works as written. `#` starts a comment. |

Custom entries are applied last, so naming `SDL_VIDEODRIVER` or `WEBKIT_DISABLE_COMPOSITING_MODE` there overrides the checkbox — the app says so rather than letting a ticked box lie. A line that is not a valid assignment is reported and skipped, never passed on silently. All three are saved with the profile.

### Keyboard shortcuts inside a session

The two clients use different defaults:

| Action | SDL client (default) | X11 client (on-screen toolbar) |
|---|---|---|
| Toggle fullscreen | `Right Shift` + `Enter` | `Ctrl` + `Alt` + `Enter` |
| Minimize | `Right Shift` + `M` | toolbar button |

---

## Troubleshooting

**dnf reports an ffmpeg conflict (`libavcodec-free` vs `ffmpeg-libs`).**
Your system has RPMFusion's full ffmpeg, which conflicts with Fedora's patent-stripped `libav*-free` packages. The script requests these headers by pkg-config capability so dnf resolves to whichever you already have. If you still hit it on an older copy of the script, install `ffmpeg-devel` and re-run with `SKIP_DEPS=1`.

**Do not use `--allowerasing` to resolve this.** It would replace RPMFusion's ffmpeg with the stripped build, degrading codec support system-wide as a side effect of building an RDP client.

**`Could NOT find Wayland (missing: XKBCOMMON_INCLUDE_DIR)`.**
Install `libxkbcommon-devel` (Fedora), `libxkbcommon-dev` (Debian/Ubuntu), or `libxkbcommon` (Arch). Wayland is a required feature in FreeRDP's uwac component, so configure stops rather than disabling it. Note this is a different package from `libxkbfile`.

**cmake warns that `jansson` and `json-c` were not found.**
FreeRDP needs a JSON parser for Entra token handling. Install `jansson-devel` (Fedora), `libjansson-dev` (Debian/Ubuntu), or `jansson` (Arch).

**`The following required packages were not found: sso-mib>=0.5.0`.**
Only happens if you forced `WITH_SSO_MIB=ON` without the library installed. The default is `auto`, which detects it and builds without it when absent. It is optional and unrelated to the sign-in window — install `sso-mib-devel` only if you use a local identity broker.

**The application shows a generic icon.**
Run `./scripts/diagnose-icon.sh`, which checks each step of the lookup chain.

The most common cause is a stale `icon-theme.cache`. Icon lookups trust that cache over scanning the directory, so a cache written before the icon was installed keeps reporting it absent:

```bash
rm -f ~/.local/share/icons/hicolor/icon-theme.cache
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
kbuildsycoca6 --noincremental
```

If the menu entry is still generic, restart the shell with `systemctl --user restart plasma-plasmashell`, which is quicker than logging out.

A second cause is a missing `~/.local/share/icons/hicolor/index.theme`. A directory without one is not a valid icon theme, so lookups skip it and a correctly installed icon never resolves. `install.sh` creates it. Fix an existing installation with:

```bash
cp /usr/share/icons/hicolor/index.theme ~/.local/share/icons/hicolor/
kbuildsycoca6 --noincremental
```

Note that on Wayland there is no per-window icon: the compositor matches the window's `app_id` to a desktop entry and reads its `Icon=` line. `QIcon.setWindowIcon` and `StartupWMClass` affect X11 only.

**Preflight reports a library as missing that you know is installed.**
pkg-config file names differ between distributions. Find the real name with `pkg-config --list-all | grep -i <library>`, then either re-run with `SKIP_PREFLIGHT=1` or open an issue with the name so it can be added to the list.

**`entrardp` fails with `No module named 'entrardp'` after an OS upgrade.**
An install made by an older `install.sh`, which used `pip install --user` and so lived under one Python version's `site-packages`. A distribution upgrade to a newer Python (Fedora 44 → 45 went from 3.14 to 3.15) leaves the launcher behind and the app out of reach. Re-run `./scripts/install.sh`; current versions install independently of the Python version and remove the old copy.

**The app reports "This build cannot start" after an OS upgrade.**
A FreeRDP you compiled is linked against the libraries that were current when you built it. An upgrade replaces some of them — Fedora 45 moved OpenSSL, FFmpeg and ICU to new versions — and the loader then refuses to start the client. The app detects this, disables Connect, and switches to another binary that works if there is one. Re-run `./scripts/install.sh`: if the distribution's FreeRDP can sign in, that is used and nothing is compiled; otherwise it rebuilds. Remove the stale build with `rm -rf ~/.local/share/entrardp/freerdp` or `sudo dnf remove freerdp-nightly` once you no longer need it.

**`/usr/bin/sdl-freerdp` is reported as having no sign-in window.**
Your distribution's package predates 3.32 or was built without a sign-in helper. Run `./scripts/install.sh`, which builds one only in that case, or use **Browse** to select another binary. Binaries are deliberately never taken from a CMake build directory, per upstream guidance, so a build tree at `~/FreeRDP/build/...` will not be detected.

**Sign-in prints a URL instead of opening a browser or window.**
The binary has no usable sign-in helper. The *FreeRDP binary* section says so in orange, and names any helper that was configured but whose program is missing from `libexec`.

**The browser opens for sign-in, but the session never continues.**
The browser helper gets the result back through a `ms-appx-web://` URL handler that FreeRDP registers in `~/.local/share/applications` on first use. If your browser asks whether to open the link, allow it. If nothing happens, check that `xdg-open` and `xdg-mime` are installed (`xdg-utils`), or pick an embedded window under *Sign-in method* if your binary has one.

**`Cannot find KDC for realm`.**
`/sec:aad` is missing. The app always sets it, so this points at a stale profile or something in *Extra flags* overriding it.

**Command line parsing failed at 'azure'.**
A quote character got pasted into a field. The app strips these automatically; if you see it, check *Extra flags*.

**Scratchy or distorted microphone audio in Teams, but fine locally.**
Check what your binary was built with:

```bash
<path-to-binary> /buildconfig | tr ' ' '\n' | grep -iE 'VERBOSE_WINPR|VAAPI_H264'
```

`WITH_VERBOSE_WINPR_ASSERT=ON` means debug instrumentation is active. FreeRDP itself warns on every connection that it "might slow down the application", and the audio capture path has deadlines tight enough for that to be audible. Rebuild with the current scripts, which disable it by default. An older copy of `build-freerdp-rpm.sh` left it on.

Note that rebuilding alone may not be enough: the spec hardcodes version `3.0-0`, so `dnf` sees an identical package name and version and skips the install, silently leaving the old binary in place. Verify with `rpm -q --qf '%{BUILDTIME:date}\n' freerdp-nightly`, and force it if needed:

```bash
sudo rpm -Uvh --force ~/rpmbuild/RPMS/x86_64/freerdp-nightly-*.rpm
```

**Horizontal line artifacts on Wayland with smart sizing.**
A FreeRDP SDL3 rendering bug ([FreeRDP#13204](https://github.com/FreeRDP/FreeRDP/issues/13204)), fixed upstream in 3.31.0. If you see it, your binary predates the fix — rebuild, or use a fixed resolution with fullscreen until you do.

**The embedded sign-in window never appears on Wayland.**
Leave *Force X11 video driver* enabled. An embedded sign-in window does not map reliably on native Wayland.

**The embedded sign-in window crashes, or appears and vanishes.**
For the WebKitGTK window, tick *Disable WebKit compositing* (`WEBKIT_DISABLE_COMPOSITING_MODE=1`); its accelerated compositing is unreliable on some GPU and driver combinations. For the Qt WebEngine window, add `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` under custom variables. Or choose *Default web browser* under *Sign-in method*.

**Host does not resolve.**
The app warns before connecting. Entra-joined machines often aren't in corporate DNS; add an `/etc/hosts` entry. Not applicable when connecting through an AVD workspace file, which carries its own host and is not resolved locally.

**AVD connection fails with `0x1608`, or stops working after months of use.**
The certificate inside the workspace file has expired. Download a fresh `.rdpw` from the [AVD web client](https://client.wvd.microsoft.com/arm/webclient/) and select it again — there is no way to renew the existing one. The app warns about files older than 90 days, but that threshold is a guess based on the file's modification time, so a younger file can still be expired.

**AVD connects to nothing, or times out reaching the session host.**
The session host in a workspace file is only reachable through the ARM gateway. The app always appends `/gateway:type:arm` when a workspace file is set — if you see this, check *Extra flags* for a conflicting `/gateway:` entry, and confirm the command preview still shows it.

---

## Project layout

```
src/entrardp/
    config.py     Flag definitions, input sanitizing, profile storage
    freerdp.py    Binary discovery, sign-in detection, command assembly;
                  also runnable as `python3 -m entrardp.freerdp`
    gui.py        PyQt6 interface
scripts/
    install.sh            Entry point: builds FreeRDP only if nothing
                          installed can sign in, installs the GUI,
                          registers the desktop entry
    build-freerdp-rpm.sh  Builds an RPM via upstream's own packaging
                          (used automatically with dnf)
    build-freerdp.sh      Compiles to a private prefix; used elsewhere
    freerdp-common.sh     Sourced by the three above: reporting, release-tag
                          resolution, and which sign-in flags a source tree
                          needs, so both build routes produce the same thing
    diagnose-icon.sh      Reports why the application icon may not appear
tests/                 Command assembly, sign-in detection, input
                       sanitizing, profile storage
data/                  Desktop entry, AppStream metainfo, icon
```

### Tests

The suite covers the pure logic and needs neither a display nor a FreeRDP binary:

```bash
pip install . pytest ruff
pytest tests/
ruff check src/ tests/
shellcheck scripts/*.sh docs/*.sh
```

CI runs those three checks on every push, plus `desktop-file-validate` and `appstreamcli` on the desktop metadata.

---

## The original guide

The step-by-step build walkthrough this project grew out of is preserved at
[docs/BUILD-GUIDE.md](docs/BUILD-GUIDE.md), along with the original `rdp-aad.sh`
wrapper script. Useful if you would rather understand each step than run a script.

## Keeping it updated

If `install.sh` found your distribution's FreeRDP good enough, there is nothing to do here: it updates with the rest of the system. This section is for builds made by this project.

That is the real tradeoff of building your own FreeRDP, and it deserves to be stated plainly. Your distribution's package receives security updates through `dnf update`. A binary you built yourself does not — **you own that**. It can also stop starting altogether after an OS upgrade, which the app will report. FreeRDP releases often, and individual releases have carried substantial batches of security fixes, with upstream telling distributors to update as soon as possible. What is currently outstanding is published in the [FreeRDP repository](https://github.com/FreeRDP/FreeRDP) — see its [security advisories](https://github.com/FreeRDP/FreeRDP/security/advisories) and [releases](https://github.com/FreeRDP/FreeRDP/releases).

The build scripts therefore resolve the **newest release tag** at build time rather than defaulting to a branch. FreeRDP tags releases directly, so this always produces a real release without hardcoding a version that goes stale:

```bash
./scripts/build-freerdp-rpm.sh   # RPM systems
./scripts/build-freerdp.sh       # everywhere else
```

To pin a specific version, or to test unreleased changes — any tag from [the repository's releases](https://github.com/FreeRDP/FreeRDP/releases), or a branch name:

```bash
FREERDP_BRANCH=<release-tag> ./scripts/build-freerdp-rpm.sh
FREERDP_BRANCH=master ./scripts/build-freerdp-rpm.sh
```

A pinned ref is taken at face value and built as asked. Only automatic resolution enforces the 3.16.0 floor below which no sign-in window exists, so pinning something older produces a working client that cannot open one — which the app then reports in its *FreeRDP binary* section.

### Staying on top of it

Check what you are running:

```bash
/opt/freerdp-nightly/bin/sdl-freerdp3 /version   # RPM route
~/.local/share/entrardp/freerdp/bin/sdl-freerdp /version   # source route
```

Then compare against the releases page linked above and rebuild when a security release lands. Watching the repository's advisories, or release announcements on [freerdp.com](https://www.freerdp.com/), is the low-effort version.

Realistically this means rebuilding a handful of times a year — not tracking every release, but not never either. An RDP client authenticates and handles untrusted network input, so it is not a good candidate for install-and-forget.

## Upstream direction

FreeRDP maintainers indicated that browser sign-in would move out of the client process into an external helper communicating over IPC ([FreeRDP#13237](https://github.com/FreeRDP/FreeRDP/issues/13237)). That landed in **3.32.0**: `WITH_WEBVIEW` is gone, replaced by helper programs speaking a documented protocol (`client/common/aad-helper-protocol.md` upstream), chosen with `/azure:auth-helper`.

The practical effect is that distributions can now ship working Entra sign-in, and Fedora 45 does. The build scripts here have become a fallback for distributions that do not yet, and for anyone who wants the embedded window rather than the browser. The GUI's job is unchanged: probe whatever binary it is pointed at and report what that binary actually supports.

## Credits

Built on [FreeRDP](https://github.com/FreeRDP/FreeRDP). The build recipe originated from working out webview-enabled Entra authentication on Fedora and Nobara; see [FreeRDP#13201](https://github.com/FreeRDP/FreeRDP/issues/13201).

## License

[The Unlicense](LICENSE): public domain, no conditions. FreeRDP itself, which this app runs but does not include, is Apache-2.0.
