"""PyQt6 front-end for Entra ID authenticated RDP sessions."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .config import APP_NAME, MUTUALLY_EXCLUSIVE, TOGGLES, ProfileStore, clean_value
from .freerdp import (
    Connection,
    Webview,
    detect_webview,
    find_binary,
    is_usable,
    resolves,
)

STATUS_COLORS = {
    "ok": "color: #27ae60;",
    "warn": "color: #e67e22;",
    "error": "color: #c0392b;",
    "muted": "color: #7f8c8d;",
}


class DnsProbeSignals(QObject):
    finished = pyqtSignal(str, bool)


class DnsProbe(QRunnable):
    """Resolve a hostname off the UI thread.

    Resolution can block for seconds on a name that does not exist, which is
    exactly what happens while someone is still typing one. Running it inline
    freezes the interface between keystrokes.
    """

    def __init__(self, host: str):
        super().__init__()
        self.host = host
        self.signals = DnsProbeSignals()

    def run(self):
        ok = resolves(self.host)
        self.signals.finished.emit(self.host, ok)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(640)
        self.store = ProfileStore()
        self.toggle_widgets: dict[str, QCheckBox] = {}
        self._pool = QThreadPool()
        self._dns_ok: dict[str, bool] = {}
        # Wait for a pause in typing before probing, so an intermediate value
        # like "m" on the way to "my-host" never triggers a lookup.
        self._dns_timer = QTimer(self)
        self._dns_timer.setSingleShot(True)
        self._dns_timer.setInterval(900)
        self._dns_timer.timeout.connect(self._start_dns_probe)
        self._build_ui()
        self._reload_profiles()
        self.bin_in.setText(find_binary() or "")
        self._on_binary_changed()
        self._restore_last_profile()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)

        prof_row = QHBoxLayout()
        self.profile_box = QComboBox()
        self.profile_box.setEditable(True)
        self.profile_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.profile_box.lineEdit().setPlaceholderText("Type a name, or pick a saved profile")
        self.profile_box.activated.connect(
            lambda i: self._apply_profile(self.profile_box.itemText(i))
        )
        self.profile_box.lineEdit().returnPressed.connect(self._save_profile)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_profile)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_profile)
        prof_row.addWidget(QLabel("Profile:"))
        prof_row.addWidget(self.profile_box, 1)
        prof_row.addWidget(save_btn)
        prof_row.addWidget(del_btn)
        root.addLayout(prof_row)

        conn = QGroupBox("Connection")
        form = QFormLayout(conn)
        self.host_in = QLineEdit()
        self.host_in.setPlaceholderText("must match the Entra-registered device name")
        self.user_in = QLineEdit()
        self.user_in.setPlaceholderText("you@yourdomain.com")
        self.tenant_in = QLineEdit()
        self.tenant_in.setPlaceholderText("00000000-0000-0000-0000-000000000000")
        for w in (self.host_in, self.user_in, self.tenant_in):
            w.textChanged.connect(self._refresh)
        form.addRow("Host name:", self.host_in)
        form.addRow("User name:", self.user_in)
        form.addRow("Tenant ID:", self.tenant_in)
        root.addWidget(conn)

        binbox = QGroupBox("FreeRDP binary")
        binlayout = QVBoxLayout(binbox)
        bin_row = QHBoxLayout()
        self.bin_in = QLineEdit()
        self.bin_in.setPlaceholderText("/path/to/sdl-freerdp")
        self.bin_in.textChanged.connect(self._on_binary_changed)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_binary)
        bin_row.addWidget(self.bin_in, 1)
        bin_row.addWidget(browse_btn)
        binlayout.addLayout(bin_row)
        self.webview_label = QLabel()
        self.webview_label.setWordWrap(True)
        binlayout.addWidget(self.webview_label)
        root.addWidget(binbox)

        opts = QGroupBox("Session options")
        cols = QHBoxLayout(opts)
        left, right = QVBoxLayout(), QVBoxLayout()
        half = (len(TOGGLES) + 1) // 2
        for i, (key, label, flag, default, tip) in enumerate(TOGGLES):
            cb = QCheckBox(label)
            cb.setChecked(default)
            cb.setToolTip(f"{flag}\n{tip}")
            cb.stateChanged.connect(self._on_toggle)
            self.toggle_widgets[key] = cb
            (left if i < half else right).addWidget(cb)
        left.addStretch()
        right.addStretch()
        cols.addLayout(left)
        cols.addLayout(right)
        root.addWidget(opts)

        res = QGroupBox("Manual resolution")
        res.setCheckable(True)
        res.setChecked(True)
        res_row = QHBoxLayout(res)
        self.res_group = res
        self.width_in = QSpinBox()
        self.width_in.setRange(640, 7680)
        self.width_in.setSingleStep(16)
        self.width_in.setValue(2560)
        self.height_in = QSpinBox()
        self.height_in.setRange(480, 4320)
        self.height_in.setSingleStep(16)
        self.height_in.setValue(1440)
        for w in (self.width_in, self.height_in):
            w.valueChanged.connect(self._refresh)
        res.toggled.connect(self._refresh)
        res_row.addWidget(QLabel("Width:"))
        res_row.addWidget(self.width_in)
        res_row.addWidget(QLabel("Height:"))
        res_row.addWidget(self.height_in)
        res_row.addStretch()
        root.addWidget(res)

        env_row = QHBoxLayout()
        self.force_x11 = QCheckBox("Force X11 video driver (SDL_VIDEODRIVER=x11)")
        self.force_x11.setChecked(True)
        self.force_x11.setToolTip(
            "Runs the client under XWayland.\n"
            "The Entra webview popup does not map reliably on native Wayland."
        )
        self.force_x11.stateChanged.connect(self._refresh)
        env_row.addWidget(self.force_x11)
        env_row.addStretch()
        root.addLayout(env_row)

        extra_row = QHBoxLayout()
        self.extra_in = QLineEdit()
        self.extra_in.setPlaceholderText("/scale:100  /timeout:30000")
        self.extra_in.textChanged.connect(self._refresh)
        extra_row.addWidget(QLabel("Extra flags:"))
        extra_row.addWidget(self.extra_in, 1)
        root.addLayout(extra_row)

        root.addWidget(QLabel("Command preview:"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(130)
        root.addWidget(self.preview)

        self.warn_label = QLabel()
        self.warn_label.setWordWrap(True)
        root.addWidget(self.warn_label)

        act = QHBoxLayout()
        self.status = QLabel("Ready")
        act.addWidget(self.status, 1)
        copy_btn = QPushButton("Copy command")
        copy_btn.clicked.connect(self._copy)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._connect)
        act.addWidget(copy_btn)
        act.addWidget(self.connect_btn)
        root.addLayout(act)

    # ------------------------------------------------------------- state
    def _connection(self) -> Connection:
        return Connection(
            binary=self.bin_in.text(),
            host=self.host_in.text(),
            username=self.user_in.text(),
            tenant_id=self.tenant_in.text(),
            toggles={k: w.isChecked() for k, w in self.toggle_widgets.items()},
            manual_res=self.res_group.isChecked(),
            width=self.width_in.value(),
            height=self.height_in.value(),
            force_x11=self.force_x11.isChecked(),
            extra=self.extra_in.text(),
        )

    def _set_status(self, text: str, kind: str = "muted"):
        self.status.setText(text)
        self.status.setStyleSheet(STATUS_COLORS.get(kind, ""))

    def _on_toggle(self):
        sender = self.sender()
        for a, b in MUTUALLY_EXCLUSIVE:
            wa, wb = self.toggle_widgets[a], self.toggle_widgets[b]
            if sender is wa and wa.isChecked() and wb.isChecked():
                wb.blockSignals(True); wb.setChecked(False); wb.blockSignals(False)
            elif sender is wb and wb.isChecked() and wa.isChecked():
                wa.blockSignals(True); wa.setChecked(False); wa.blockSignals(False)
        self._refresh()

    def _refresh(self):
        conn = self._connection()
        self.preview.setPlainText(" \\\n    ".join(conn.command()))
        host = clean_value(self.host_in.text())
        blocking = not is_usable(clean_value(self.bin_in.text())) or not host
        self.connect_btn.setEnabled(not blocking)

        # problems() is called without check_dns: it must never block here.
        # Suppress warnings about fields the user has not filled in yet;
        # nagging about an empty box before it is touched is just noise.
        suppress = ("not found", "No host")
        if not clean_value(self.tenant_in.text()):
            suppress += ("No tenant ID",)
        warnings = [
            p for p in conn.problems()
            if not any(s in p for s in suppress)
        ]
        if host and self._dns_ok.get(host) is False:
            warnings.append(
                f"'{host}' does not resolve via DNS or /etc/hosts. "
                "It must match the Entra-registered device name exactly."
            )
        if warnings:
            self.warn_label.setText(" · ".join(warnings))
            self.warn_label.setStyleSheet(STATUS_COLORS["warn"])
        else:
            self.warn_label.clear()

        if host and host not in self._dns_ok:
            self._dns_timer.start()

    def _start_dns_probe(self):
        host = clean_value(self.host_in.text())
        if not host or host in self._dns_ok:
            return
        probe = DnsProbe(host)
        probe.signals.finished.connect(self._dns_result)
        self._pool.start(probe)

    def _dns_result(self, host: str, ok: bool):
        self._dns_ok[host] = ok
        # Only refresh if the field still holds the name that was probed.
        if clean_value(self.host_in.text()) == host:
            self._refresh()

    def _browse_binary(self):
        current = clean_value(self.bin_in.text())
        start = str(Path(current).parent) if current else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Select FreeRDP binary", start)
        if path:
            self.bin_in.setText(path)

    def _on_binary_changed(self):
        path = clean_value(self.bin_in.text())
        if not path:
            self._set_label("No binary selected. Run the installer, or browse to one.", "error")
        elif not is_usable(path):
            self._set_label("Not found, or not executable.", "error")
        else:
            state = detect_webview(path)
            if state is Webview.YES:
                self._set_label("WebView support detected — sign-in opens an embedded browser.", "ok")
            elif state is Webview.NO:
                self._set_label(
                    "WITH_WEBVIEW=OFF in this build. Sign-in will fall back to printing a "
                    "URL for manual copy/paste. Distribution packages commonly ship this "
                    "way; run scripts/build-freerdp.sh, or select a build made with "
                    "WITH_WEBVIEW=ON (FreeRDP 3.16.0 or newer).",
                    "warn",
                )
            else:
                self._set_label("Could not determine WebView support.", "muted")
        self._refresh()

    def _set_label(self, text: str, kind: str):
        self.webview_label.setText(text)
        self.webview_label.setStyleSheet(STATUS_COLORS.get(kind, ""))

    # ------------------------------------------------------------ actions
    def _copy(self):
        QApplication.clipboard().setText(" ".join(self._connection().command()))
        self._set_status("Command copied to clipboard", "ok")

    def _connect(self):
        args = self._connection().command()
        try:
            proc = subprocess.Popen(
                args, start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as exc:
            QMessageBox.critical(self, "Launch failed", f"Could not start {args[0]}\n\n{exc}")
            return
        self._set_status("Starting session...", "muted")
        QTimer.singleShot(2500, lambda: self._check(proc))

    def _check(self, proc):
        if proc.poll() is None:
            self._set_status(f"Session running (pid {proc.pid})", "ok")
            return
        try:
            output = proc.communicate(timeout=2)[0] or ""
        except subprocess.TimeoutExpired:
            output = ""
        self._set_status(f"Exited immediately (code {proc.returncode})", "error")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Session ended immediately")
        box.setText(f"FreeRDP exited with code {proc.returncode}.")
        box.setInformativeText("Output below.")
        box.setDetailedText("\n".join(output.strip().splitlines()[-30:]) or "(no output)")
        box.exec()

    # ----------------------------------------------------------- profiles
    def _reload_profiles(self):
        typed = self.profile_box.currentText()
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        self.profile_box.addItems(self.store.names())
        self.profile_box.setCurrentText(typed)
        self.profile_box.blockSignals(False)

    def _restore_last_profile(self):
        """Reopen with the profile that was last saved or loaded.

        Returns quietly when nothing was recorded, or when the recorded
        profile has since been deleted.
        """
        name = self.store.last_used
        if not name:
            return
        self.profile_box.setCurrentText(name)
        self._apply_profile(name)

    def _apply_profile(self, name: str):
        data = self.store.profiles.get(name)
        if not data:
            return
        self.host_in.setText(data.get("host", ""))
        self.user_in.setText(data.get("user", ""))
        self.tenant_in.setText(data.get("tenant", ""))
        if data.get("binary"):
            self.bin_in.setText(data["binary"])
        for key, widget in self.toggle_widgets.items():
            widget.blockSignals(True)
            widget.setChecked(data.get("toggles", {}).get(key, widget.isChecked()))
            widget.blockSignals(False)
        self.res_group.setChecked(data.get("manual_res", True))
        self.width_in.setValue(data.get("width", 2560))
        self.height_in.setValue(data.get("height", 1440))
        self.force_x11.setChecked(data.get("force_x11", True))
        self.extra_in.setText(data.get("extra", ""))
        self.store.last_used = name
        self._refresh()
        self._set_status(f"Loaded '{name}'", "muted")

    def _save_profile(self):
        name = clean_value(self.profile_box.currentText())
        if not name:
            self._set_status("Enter a profile name before saving", "warn")
            self.profile_box.setFocus()
            return
        if name in self.store.profiles and QMessageBox.question(
            self, "Overwrite profile", f"'{name}' already exists. Overwrite it?"
        ) != QMessageBox.StandardButton.Yes:
            return
        conn = self._connection()
        self.store.put(name, {
            "host": clean_value(conn.host),
            "user": clean_value(conn.username),
            "tenant": clean_value(conn.tenant_id),
            "binary": clean_value(conn.binary),
            "toggles": conn.toggles,
            "manual_res": conn.manual_res,
            "width": conn.width,
            "height": conn.height,
            "force_x11": conn.force_x11,
            "extra": clean_value(conn.extra),
        })
        self.store.last_used = name
        self._reload_profiles()
        self.profile_box.setCurrentText(name)
        self._set_status(f"Saved '{name}'", "ok")

    def _delete_profile(self):
        name = clean_value(self.profile_box.currentText())
        if name not in self.store.profiles:
            self._set_status("No saved profile by that name", "warn")
            return
        if QMessageBox.question(self, "Delete profile", f"Delete '{name}'?") != \
                QMessageBox.StandardButton.Yes:
            return
        self.store.delete(name)
        if self.store.last_used == name:
            self.store.last_used = None
        self.profile_box.setCurrentText("")
        self._reload_profiles()
        self.profile_box.setCurrentText("")
        self._set_status(f"Deleted '{name}'", "muted")
