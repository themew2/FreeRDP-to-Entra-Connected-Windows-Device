# Entra RDP

A desktop app for connecting to **Microsoft Entra ID (Azure AD) joined Windows machines** from Linux, with native web account sign-in — the equivalent of the *"Use a web account to sign in to the remote computer"* checkbox in Windows' `mstsc.exe`.

![Entra RDP](data/screenshots/main-window.png)

---

## The problem this solves

Distribution packages generally enable everything Entra sign-in needs **except one flag**. On Fedora 44:

```console
$ /usr/bin/sdl-freerdp /buildconfig | tr ' ' '\n' | grep -iE 'WITH_AAD|WITH_PULSE|WITH_SSO_MIB|WITH_WEBVIEW'
WITH_AAD=ON
WITH_PULSE=ON
WITH_SSO_MIB=ON
WITH_WEBVIEW=OFF
```

`WITH_WEBVIEW=OFF` is the whole problem. Without it, signing in degrades to a copy-and-paste ritual: the client prints a login URL, you open it in a browser, sign in, then paste the redirect URL back into the terminal. With it, a browser window opens inside the app and sign-in just works.

This is not distribution carelessness. FreeRDP pulls its webview helper (`akallabeth/webview`) via CMake FetchContent at configure time, and packaging policies commonly forbid fetching sources during a build. Enabling it in a package would require that helper to be packaged independently first.

Support landed upstream in **FreeRDP 3.16.0**, so anything older cannot have it regardless of configuration. Newer packages vary — run the check above on your own system before compiling anything.

Both builds are named `sdl-freerdp`. **Name equality is not build equality** — which is why this app probes the binary it is about to run and tells you which one you have before you connect.

---

## Platform support

| Platform | Status |
|---|---|
| **Fedora 44** | Tested end to end via both paths |
| Other Fedora / RHEL / openSUSE | Should work; builds from upstream's own spec |
| Debian / Ubuntu / Arch | **Untested.** Falls back to the source build, whose dependency lists have only been exercised on Fedora |
| Anything else | No automatic FreeRDP build. Supply your own binary and use `SKIP_FREERDP=1` |

The GUI itself is distribution-independent — it needs a FreeRDP binary built with `WITH_WEBVIEW=ON` and does not care where that came from. Only the FreeRDP build differs by platform.

On untested platforms the source build runs a preflight check through `pkg-config` before compiling, so a wrong package name produces a precise list of what is missing rather than an obscure build failure. Reports of what was actually needed on your distribution are welcome.

## Install

```bash
git clone https://github.com/themew2/FreeRDP-to-Entra-Connected-Windows-Device.git
cd FreeRDP-to-Entra-Connected-Windows-Device
./scripts/install.sh
```

One command. It builds a webview-enabled FreeRDP, installs the GUI, and registers the desktop entry. Budget 20–40 minutes, almost all of it compiling. Only the dependency step needs `sudo`.

**On RPM systems** it builds through FreeRDP's own packaging, producing a package `dnf` tracks, installed to `/opt/freerdp-nightly`. This is the route the FreeRDP maintainers suggested ([FreeRDP#13237](https://github.com/FreeRDP/FreeRDP/issues/13237)).

**Elsewhere** it compiles into a private prefix under `~/.local/share/entrardp`. Its dependency lists are maintained here rather than upstream and have only been exercised on Fedora — see [Platform support](#platform-support).

Both routes build the **same tagged release** of FreeRDP. They differ in packaging, not in version.

**Already have a webview-enabled FreeRDP?** Skip the compile:

```bash
SKIP_FREERDP=1 ./scripts/install.sh
```

Then select your binary in the app's *FreeRDP binary* field. It will tell you whether that build supports webview.

Both routes produce a release build by default. To reproduce upstream's nightly test configuration instead — debug, AddressSanitizer, verbose assertions — set `RELEASE_BUILD=0`. That is only useful when filing a bug against FreeRDP itself, and it noticeably degrades performance.

## What the scripts do

`install.sh` is the entry point and picks a FreeRDP build route for you. The other two are the routes themselves, and can be run directly.

```
install.sh
  ├─ build-freerdp-rpm.sh   on RPM systems
  ├─ build-freerdp.sh       everywhere else
  ├─ python3-pip, python3-pyqt6   installed if missing
  ├─ pip install                  the app, to ~/.local/bin/entrardp
  └─ desktop entry, icons, AppStream metainfo
```

### `scripts/build-freerdp-rpm.sh` — RPM systems

Builds an RPM using FreeRDP's own `packaging/rpm/freerdp-nightly.spec` and `packaging/scripts/create_rpm.sh`, patching the spec to set `WITH_WEBVIEW=ON`. The spec already declares the WebKitGTK build dependency.

What it gains you over the source route:

- `dnf` owns the result, so `dnf remove freerdp-nightly` uninstalls cleanly
- build dependencies come from the spec via `dnf builddep`, not a list maintained here
- installs to `/opt/freerdp-nightly`, coexisting with your distribution's FreeRDP
- the binary is named `sdl-freerdp3`, since the nightly spec sets `WITH_CLIENT_SDL_VERSIONED=ON`

**The "nightly" in the file name refers to how upstream publishes test packages, not to the branch built.** The source is the same tagged release the other script uses. What differs is the spec's build configuration, which is tuned for nightly testing: debug build, AddressSanitizer, `WITH_VERBOSE_WINPR_ASSERT`, experimental VAAPI H264 encoding. This script turns all of that off by default, because a build carrying it produced audibly degraded microphone capture in Teams calls where a plain release build of the same tag did not.

| Variable | Default | Purpose |
|---|---|---|
| `RELEASE_BUILD=0` | on | Build upstream's nightly test configuration instead. Slower, and known to degrade audio. Use only when reporting a bug to FreeRDP |
| `AUTO_INSTALL=0` | on | Build the RPM but do not install it |
| `FREERDP_BRANCH` | newest release tag | Branch or tag to build. Must be ≥ 3.16.0 for webview support |

### `scripts/build-freerdp.sh` — everywhere else

Compiles FreeRDP directly into `~/.local/share/entrardp/freerdp`, a private prefix that will not collide with your distribution's package. Used on systems without RPM tooling, and usable anywhere if you would rather not involve the package manager.

The app prefers a binary from this prefix when both are installed, since it is built with plain release defaults and nothing else.

| Phase | What happens |
|---|---|
| Dependencies | Detects `dnf` / `apt` / `pacman` and installs the toolchain and headers |
| Preflight | Verifies every prerequisite with `pkg-config` and reports *all* missing ones at once |
| Configure | `-DWITH_WEBVIEW=ON -DWITH_AAD=ON -DWITH_PULSE=ON`, `WITH_SSO_MIB` auto-detected |
| Gate | Aborts unless CMakeCache confirms the flags actually initialised |
| Build & install | `cmake --build`, then `--target install` |
| Verify | Runs `/buildconfig` on the installed binary |

The repeated checks exist because the failure they prevent is a *silent* one: CMake accepts a flag it has not yet defined without complaint, so a build can succeed, install cleanly, run fine, and simply lack the feature you asked for.

Its dependency lists are maintained in this repository and have only been exercised on Fedora. The `apt` and `pacman` branches print a warning to that effect.

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
pip uninstall entrardp
rm -f ~/.local/share/applications/io.github.themew2.EntraRDP.desktop
rm -rf ~/.config/entrardp ~/.cache/entrardp
```

Then remove FreeRDP, depending on which route was used:

```bash
sudo dnf remove freerdp-nightly        # RPM route
rm -rf ~/.local/share/entrardp         # source route
```

---

### Before compiling: check what you already have

An existing build may already do the job:

- **Nightly packages** install to `/opt/freerdp-nightly` and coexist with your distribution package. See [PreBuilds](https://github.com/FreeRDP/FreeRDP/wiki/PreBuilds).
- **RPM users** can build packages from the FreeRDP checkout using the scripts in its `packaging/scripts` directory.
- **Flathub** ships `com.freerdp.FreeRDP`, with a beta channel available.

Check any of them with:

```bash
<path-to-binary> /buildconfig | tr ' ' '\n' | grep -i WITH_WEBVIEW
```

`WITH_WEBVIEW=ON` means you can skip the compile entirely — use `SKIP_FREERDP=1` and select that binary in the app.

---

## Usage

Fill in three fields and press Connect:

| Field | Notes |
|---|---|
| **Host name** | Must match the Entra-registered device name **exactly**, and must resolve via DNS or `/etc/hosts`. |
| **User name** | `you@yourdomain.com` |
| **Tenant ID** | Your Entra tenant GUID, from Azure Portal → Microsoft Entra ID → Overview. |

Save the combination as a named profile to reuse it. The app reopens on whichever profile you last saved or loaded.

Profiles live in `~/.config/entrardp/profiles.json` and the last-used name in `~/.config/entrardp/state.json`, both mode `600`.

**No credentials are ever stored.** Authentication happens entirely inside the Entra webview. The app keeps only hostnames, usernames, and tenant IDs.

### Session options

Every checkbox maps to exactly one FreeRDP flag, shown in its tooltip and reflected live in the command preview. Nothing is hidden — if the app misbehaves, copy the previewed command and run it in a terminal to see the raw output.

Defaults match a verified-working configuration: fullscreen, fixed resolution, certificate bypass, audio in and out, and clipboard sharing.

### Keyboard shortcuts inside a session

These are SDL client defaults, and differ from the older `xfreerdp` client:

| Keys | Action |
|---|---|
| `Right Shift` + `Enter` | Toggle fullscreen |
| `Right Shift` + `M` | Minimize |

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
Only happens if you forced `WITH_SSO_MIB=ON` without the library installed. The default is `auto`, which detects it and builds without it when absent. It is optional and unrelated to webview sign-in — install `sso-mib-devel` only if you use a local identity broker.

**The application shows a generic icon.**
Run `./scripts/diagnose-icon.sh`, which checks each step of the lookup chain.

The most common cause is a stale `icon-theme.cache`. Icon lookups trust that cache over scanning the directory, so a cache written before the icon was installed keeps reporting it absent:

```bash
rm -f ~/.local/share/icons/hicolor/icon-theme.cache
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
kbuildsycoca6 --noincremental
```

If the menu entry is still generic, restart the shell with `systemctl --user restart plasma-plasmashell`, which is quicker than logging out.

A second cause is a missing `~/.local/share/icons/hicolor/index.theme`. A directory without one is not a valid icon theme, so lookups skip it and a correctly installed icon never resolves. `install.sh` now creates it. Fix an existing installation with:

```bash
cp /usr/share/icons/hicolor/index.theme ~/.local/share/icons/hicolor/
kbuildsycoca6 --noincremental
```

Note that on Wayland there is no per-window icon: the compositor matches the window's `app_id` to a desktop entry and reads its `Icon=` line. `QIcon.setWindowIcon` and `StartupWMClass` affect X11 only.

**Preflight reports a library as missing that you know is installed.**
pkg-config file names differ between distributions. Find the real name with `pkg-config --list-all | grep -i <library>`, then either re-run with `SKIP_PREFLIGHT=1` or open an issue with the name so it can be added to the list.

**`/usr/bin/python3: No module named pip`.**
Some distributions do not install pip with Python. `install.sh` now handles this, but to do it by hand: `sudo dnf install python3-pip python3-pyqt6`.

**`error: externally-managed-environment`.**
The system Python is marked externally managed (PEP 668). `install.sh` retries automatically with `--break-system-packages`, which only affects `~/.local`, never system packages.

**The app finds `/usr/bin/sdl-freerdp` and warns about WebView.**
Expected before you have run `build-freerdp.sh`. Binaries are deliberately never taken from a CMake build directory, per upstream guidance, so a build tree at `~/FreeRDP/build/...` will not be detected. Either run `./scripts/build-freerdp.sh` to install into the private prefix, or use **Browse** to select a binary explicitly.

**Sign-in prints a URL instead of opening a browser window.**
Your FreeRDP binary lacks webview support. The *FreeRDP binary* section will say so in orange. Either run `./scripts/build-freerdp.sh`, or browse to a build made with `WITH_WEBVIEW=ON`.

**`Cannot find KDC for realm`.**
`/sec:aad` is missing. The app always sets it, so this points at a stale profile or something in *Extra flags* overriding it.

**Command line parsing failed at 'azure'.**
A quote character got pasted into a field. The app strips these automatically now; if you see it, check *Extra flags*.

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

**Horizontal line artifacts on Wayland.**
Smart sizing combined with fullscreen is a FreeRDP SDL3 rendering bug, tracked upstream as [FreeRDP#13204](https://github.com/FreeRDP/FreeRDP/issues/13204). The app warns when both are enabled — use a fixed resolution with fullscreen instead.

**The sign-in window never appears on Wayland.**
Leave *Force X11 video driver* enabled. The webview popup does not map reliably on native Wayland.

**Host does not resolve.**
The app warns before connecting. Entra-joined machines often aren't in corporate DNS; add an `/etc/hosts` entry.

---

## Project layout

```
src/entrardp/
    config.py     Flag definitions, input sanitizing, profile storage
    freerdp.py    Binary discovery, webview detection, command assembly
    gui.py        PyQt6 interface
scripts/
    install.sh            Entry point: builds FreeRDP, installs the GUI,
                          registers the desktop entry
    build-freerdp-rpm.sh  Builds an RPM via upstream's own packaging
                          (used automatically on RPM systems)
    build-freerdp.sh      Compiles to a private prefix; used elsewhere,
                          and preferred by the app when both are present
    diagnose-icon.sh      Reports why the application icon may not appear
data/                  Desktop entry, AppStream metainfo, icon
```

---

## The original guide

The step-by-step build walkthrough this project grew out of is preserved at
[docs/BUILD-GUIDE.md](docs/BUILD-GUIDE.md), along with the original `rdp-aad.sh`
wrapper script. Useful if you would rather understand each step than run a script.

## Keeping it updated

This is the real tradeoff of building your own FreeRDP, and it deserves to be stated plainly.

Your distribution's package receives security updates through `dnf update`. A binary you built yourself does not — **you own that**. FreeRDP releases often and security fixes are substantial: 3.31.0 alone addressed 22 security advisories, with upstream telling distributors to update as soon as possible.

The build scripts therefore resolve the **newest release tag** at build time rather than defaulting to a branch. FreeRDP tags releases directly, so this always produces a real release without hardcoding a version that goes stale:

```bash
./scripts/build-freerdp-rpm.sh   # RPM systems
./scripts/build-freerdp.sh       # everywhere else
```

To pin a specific version, or to test unreleased changes:

```bash
FREERDP_BRANCH=3.31.1 ./scripts/build-freerdp-rpm.sh
FREERDP_BRANCH=master ./scripts/build-freerdp-rpm.sh
```

### Staying on top of it

Check what you are running:

```bash
/opt/freerdp-nightly/bin/sdl-freerdp3 /version   # RPM route
~/.local/share/entrardp/freerdp/bin/sdl-freerdp /version   # source route
```

Then compare against [FreeRDP releases](https://github.com/FreeRDP/FreeRDP/releases) and rebuild when a security release lands. Watching [security advisories](https://github.com/FreeRDP/FreeRDP/security) or release announcements on [freerdp.com](https://www.freerdp.com/) is the low-effort version.

Realistically this means rebuilding a handful of times a year — not tracking every release, but not never either. An RDP client authenticates and handles untrusted network input, so it is not a good candidate for install-and-forget.

## Upstream direction

FreeRDP maintainers have indicated that browser-based authentication may eventually move out of the client process entirely, into an external helper communicating over IPC — the approach already used by `SSO_MIB` ([FreeRDP#13237](https://github.com/FreeRDP/FreeRDP/issues/13237)).

If that lands, `WITH_WEBVIEW` as a compile-time flag becomes less central, and this project's build scripts may be unnecessary. The GUI itself is unaffected: it probes whatever binary it is pointed at and reports what that binary supports, rather than assuming a particular mechanism.

## Credits

Built on [FreeRDP](https://github.com/FreeRDP/FreeRDP). The build recipe originated from working out webview-enabled Entra authentication on Fedora and Nobara; see [FreeRDP#13201](https://github.com/FreeRDP/FreeRDP/issues/13201).

## License

Apache-2.0, matching FreeRDP.
