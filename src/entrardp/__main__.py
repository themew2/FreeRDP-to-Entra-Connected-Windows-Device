"""Entry point: python -m entrardp, or the `entrardp` console script."""

import sys

from .config import APP_ID, APP_NAME, find_icon


def main() -> int:
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from .gui import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # Wayland maps a window to its desktop entry through this; without it the
    # compositor cannot find the icon and falls back to a generic placeholder.
    app.setDesktopFileName(APP_ID)

    # Prefer the themed icon so the desktop's own iconography wins, and fall
    # back to the file shipped with the package.
    #
    # isNull() is not a reliable test here: QIcon.fromTheme can return a
    # non-null icon that renders nothing when the name is not in the theme.
    # hasThemeIcon is the actual question, and the pixmap check catches the
    # remaining cases where a theme entry exists but produces no image.
    icon = QIcon()
    if QIcon.hasThemeIcon(APP_ID):
        icon = QIcon.fromTheme(APP_ID)
    if icon.isNull() or icon.pixmap(48, 48).isNull():
        path = find_icon()
        if path:
            icon = QIcon(path)
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
