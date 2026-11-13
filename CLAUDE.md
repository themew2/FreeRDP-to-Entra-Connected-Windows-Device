# Entra RDP: context for Claude Code

A PyQt6 front end for FreeRDP that connects to Microsoft Entra ID joined Windows machines and Azure Virtual Desktop, with web account sign-in. The README covers usage and the build scripts. This file covers what isn't obvious from the code, and where work stands.

## Background you need

- **FreeRDP has had three sign-in generations.** Before 3.16 there's no sign-in window. 3.16–3.31 has `WITH_WEBVIEW`, an embedded WebKitGTK window inside the client. **3.32.0 removed `WITH_WEBVIEW`** and added separate helper programs: `WITH_XDG_AAD_AUTH_HELPER` (default browser), `WITH_WEBVIEW_AAD_AUTH_HELPER` (embedded WebKitGTK), and `WITH_QT_AAD_AUTH_HELPER`. All three default to OFF. They install to `libexec` and are chosen with `/azure:tenantid:X,auth-helper:<path|autodetect>`.
- **Never pass `-DWITH_WEBVIEW=ON` to a 3.32+ source.** CMake caches an unknown `-D` without complaint, so the build succeeds with no sign-in at all. Checking `CMakeCache.txt` doesn't catch it either. The build scripts check that `client/common/<name>-aad-helper` was configured instead.
- **Fedora's package:** the user filed [rhbz#2520877](https://bugzilla.redhat.com/show_bug.cgi?id=2520877), and Fedora enabled the XDG (browser) helper. As of 2026-11-12, Fedora 45 stable has it (3.33.1). Fedora 43 and 44 have 3.33.1 with the helper in **updates-testing only**, and Fedora 44 stable is 3.32.2 with no helper. The WebKitGTK helper isn't packaged, because it depends on an unmerged fork of the webview library.
- **The SDL client ignores `/floatbar`.** The on-screen toolbar option switches the command to `xfreerdp` from the same directory (`find_x11_client`). Its text uses an X core font and looks rough. The user has accepted that.
- **FreeRDP's SDL dialogs are always on top** (`SDL_WINDOW_ALWAYS_ON_TOP`). With *Force X11* ticked they cover the browser during sign-in, so users should untick it when signing in through the browser.

## Decisions already made by the user

- Prefer the distribution's FreeRDP. Compile only when nothing installed can sign in.
- Builds made by this project enable **both** the XDG and WebKitGTK helpers.
- The license is **the Unlicense** everywhere: `LICENSE`, `pyproject.toml`, the metainfo, and the README.
- The settings UI uses three tabs: Connection, Settings, Advanced.
- The README presents the app first. Building FreeRDP is a fallback section, and the old guide is kept as history in `docs/BUILD-GUIDE.md`.

## Working on this repo

- Tests: `pytest tests/` (no display or FreeRDP needed), `ruff check src/ tests/`, `shellcheck scripts/*.sh docs/*.sh`. CI runs these on pull requests, plus `appstreamcli validate` and `desktop-file-validate`. CI does **not** run `ruff format`. Several files predate it, so don't reformat unrelated code.
- Run the app from a checkout: `PYTHONPATH=src python3 -m entrardp`. The installed copy in `~/.local/share/entrardp/app` only changes when `scripts/install.sh` is run again.
- Check what a FreeRDP binary can do: `PYTHONPATH=src python3 -m entrardp.freerdp [binary...]`. Exits 0 if any binary can sign in.
- If the shell is VS Code's Flatpak sandbox (no `rpm`, a different `python3`), run host commands through `flatpak-spawn --host bash -lc '...'`. The sandbox's `/tmp` isn't visible to the host.
- Commits are authored as `TheMew2`. Check `git config user.name` before committing on a new machine.

## Where work stands (2026-11-12)

Branch `fix/fedora-45`, [PR #5](https://github.com/themew2/FreeRDP-to-Entra-Connected-Windows-Device/pull/5), open into `main` with CI passing. It covers:

- support for 3.32+ sign-in helpers
- detecting builds that can't start after an OS upgrade, and switching away from them
- a sign-in method selector and the on-screen toolbar option
- the tabbed settings UI
- an installer that installs the app independently of the Python version
- the README rewrite and screenshots
- version 0.2.0

**Next: test on Fedora 44.** Nothing has been run on Fedora 44 since these changes. On that machine:

1. `PYTHONPATH=src python3 -m entrardp.freerdp` to see what the installed FreeRDP reports.
2. `./scripts/install.sh`. Expected flow: the installed FreeRDP can't sign in, so the installer updates `freerdp` from stable, which gives 3.32.2 and still can't sign in. It then updates `freerdp` alone from `updates-testing`, which gives 3.33.1, and stops **without compiling**. If the update has reached stable by then, the first update is enough.
3. If an older self-built FreeRDP exists (`/opt/freerdp-nightly` or `~/.local/share/entrardp/freerdp`), the app prefers it when it can sign in. Check which binary the *Advanced* tab selects.
4. Connect and confirm browser sign-in works. Try the on-screen toolbar too.

**Still open:**

- Update the GitHub repository description, which still describes the old build-focused purpose. Suggested wording: "Desktop app for connecting to Entra ID joined Windows machines and Azure Virtual Desktop from Linux with FreeRDP and web sign-in". This only changes with the user's approval.
- Merge PR #5 after testing on Fedora 44. The README, screenshots and metainfo image URLs only appear on GitHub once it's merged into `main`.
- Not started, offered to the user: clearer messages when sign-in fails, and support for extra flags containing spaces. Patches to FreeRDP itself were also offered (the toolbar font, and dialogs covering the browser).
