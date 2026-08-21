#!/usr/bin/env bash
# Reports why the application icon may not be appearing.
# Icon resolution has several independent failure points; this checks each.

APP_ID="io.github.themew2.EntraRDP"
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }
note() { printf '  \033[1;33m·\033[0m %s\n' "$*"; }

echo "Session"
note "type: ${XDG_SESSION_TYPE:-unknown}   desktop: ${XDG_CURRENT_DESKTOP:-unknown}"

echo "Desktop entry"
DESK="$HOME/.local/share/applications/$APP_ID.desktop"
if [[ -f "$DESK" ]]; then
    ok "present: $DESK"
    grep -q "^Icon=$APP_ID" "$DESK" && ok "Icon= line correct" || bad "Icon= line missing or wrong"
    if grep -q "^StartupWMClass=" "$DESK"; then
        ok "StartupWMClass=$(grep '^StartupWMClass=' "$DESK" | cut -d= -f2-)"
    else
        bad "no StartupWMClass (X11 windows will not match this entry)"
    fi
else
    bad "missing: $DESK  — run ./scripts/install.sh"
fi

echo "Icon file"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
[[ -f "$ICON" ]] && ok "present: $ICON" || bad "missing: $ICON  — run ./scripts/install.sh"

echo "Icon theme index"
# A directory without index.theme is not a theme, and lookups skip it, so a
# correctly installed icon can still never resolve.
IDX="$HOME/.local/share/icons/hicolor/index.theme"
if [[ -f "$IDX" ]]; then
    ok "present: $IDX"
else
    bad "missing: $IDX"
    note "without it the whole ~/.local icon directory is ignored"
    note "fix: cp /usr/share/icons/hicolor/index.theme ~/.local/share/icons/hicolor/"
fi

echo "Bundled icon"
python3 -c "
from entrardp.config import find_icon
p = find_icon()
print(('  \033[1;32m✓\033[0m bundled icon: ' + p) if p else '  \033[1;31m✗\033[0m no icon found by find_icon()')
" 2>/dev/null || bad "could not import entrardp"

echo "Theme lookup"
python3 -c "
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
app = QApplication([])
found = QIcon.hasThemeIcon('$APP_ID')
print(('  \033[1;32m✓\033[0m' if found else '  \033[1;33m·\033[0m') +
      f' hasThemeIcon: {found}' + ('' if found else '  (falls back to the bundled file)'))
" 2>/dev/null || bad "PyQt6 not importable"

echo "Icon cache"
CACHE="$HOME/.local/share/icons/hicolor/icon-theme.cache"
ICONPNG="$HOME/.local/share/icons/hicolor/48x48/apps/$APP_ID.png"
if [[ -f "$CACHE" ]]; then
    if [[ -f "$ICONPNG" && "$CACHE" -ot "$ICONPNG" ]]; then
        bad "cache is older than the installed icon: lookups will not see it"
        note "fix: rm -f \"$CACHE\" && gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor"
    else
        ok "cache present and newer than the icon"
    fi
else
    note "no cache; lookups scan the directory directly (fine)"
fi

echo "Window class (start the app first)"
if command -v xprop >/dev/null; then
    note "run: xprop WM_CLASS   then click the Entra RDP window"
    note "StartupWMClass must equal the SECOND string xprop reports"
else
    note "xprop not installed: sudo dnf install xorg-x11-utils"
fi
