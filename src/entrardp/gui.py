"""PyQt6 front-end for Entra ID authenticated RDP sessions."""

from __future__ import annotations

import subprocess
import tempfile
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


class WebviewProbeSignals(QObject):
    # Webview is an Enum, so the payload is typed as a plain object.
    finished = pyqtSignal(str, object)


class WebviewProbe(QRunnable):
    """Inspect one binary's build flags off the UI thread.

    detect_webview shells out to /buildconfig, ldd and nm. `nm` over a FreeRDP
    binary is slow enough to stall the interface visibly, and this used to run
    inline on every keystroke in the binary field.
    """

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.signals = WebviewProbeSignals()

    def run(self):
        self.signals.finished.emit(self.path, detect_webview(self.path))


class BinarySearchSignals(QObject):
    finished = pyqtSignal(str)


class BinarySearch(QRunnable):
    """Locate a FreeRDP binary off the UI thread.

    find_binary calls detect_webview once per candidate, so it carries the
    cost described above several times over — far too slow to run while the
    window is being constructed.
    """

    def __init__(self):
        super().__init__()
        self.signals = BinarySearchSignals()

    def run(self):
        self.signals.finished.emit(find_binary() or "")


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(640)
        self.store = ProfileStore()
        self.toggle_widgets: dict[str, QCheckBox] = {}
        self._pool = QThreadPool()
        self._dns_ok: dict[str, bool] = {}
        self._webview: dict[str, Webview] = {}
        self._search_pending = False
        # Wait for a pause in typing before probing, so an intermediate value
        # like "m" on the way to "my-host" never triggers a lookup.
        self._dns_timer = QTimer(self)
        self._dns_timer.setSingleShot(True)
        self._dns_timer.setInterval(900)
        self._dns_timer.timeout.connect(self._start_dns_probe)
        # Same debounce for the binary field, for the same reason.
        self._bin_timer = QTimer(self)
        self._bin_timer.setSingleShot(True)
        self._bin_timer.setInterval(400)
        self._bin_timer.timeout.connect(self._start_webview_probe)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._drain)
        self._build_ui()
        self._reload_profiles()
        self._on_binary_changed()
        # Restore first, then detect: a profile's own path is a cheap stat,
        # while detection shells out to every candidate. Detection only runs
        # if the restore left the field empty.
        self._restore_last_profile()
        if not clean_value(self.bin_in.text()):
            self._autodetect_binary()

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
        # Azure Virtual Desktop hands out a workspace file instead of a
        # host name. FreeRDP 3 reads .rdpw natively, so this is a path,
        # not something the app parses.
        self.workspace_in = QLineEdit()
        self.workspace_in.setPlaceholderText(
            "optional: .rdpw from the AVD web client, replaces the host name"
        )
        self.workspace_in.textChanged.connect(self._on_workspace_changed)
        ws_browse = QPushButton("Browse...")
        ws_browse.clicked.connect(self._browse_workspace)
        ws_clear = QPushButton("Clear")
        ws_clear.clicked.connect(lambda: self.workspace_in.clear())
        ws_row = QHBoxLayout()
        ws_row.addWidget(self.workspace_in, 1)
        ws_row.addWidget(ws_browse)
        ws_row.addWidget(ws_clear)
        form.addRow("Host name:", self.host_in)
        form.addRow("AVD workspace:", ws_row)
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
            workspace_file=self.workspace_in.text(),
        )

    def _drain(self):
        """Stop scheduling probes and let any in flight finish.

        Without this, shutting down mid-probe aborts the process: the pool
        thread is still inside run() when Python tears down the QObject
        carrying that runnable's signals, and the emit raises "wrapped C/C++
        object has been deleted". Closing the window during a slow DNS lookup
        reproduces it about half the time.

        Hooked to QApplication.aboutToQuit rather than closeEvent alone,
        because shutdown does not always go through the window: quitting the
        application directly leaves the event loop without ever closing it.

        The bound is generous enough for a DNS lookup or a /buildconfig
        probe; exceeding it only returns to the previous behaviour.
        """
        self._dns_timer.stop()
        self._bin_timer.stop()
        self._pool.waitForDone(5000)

    def closeEvent(self, event):
        self._drain()
        super().closeEvent(event)

    def _set_status(self, text: str, kind: str = "muted"):
        self.status.setText(text)
        self.status.setStyleSheet(STATUS_COLORS.get(kind, ""))

    def _on_toggle(self):
        sender = self.sender()
        for a, b in MUTUALLY_EXCLUSIVE:
            wa, wb = self.toggle_widgets[a], self.toggle_widgets[b]
            if sender is wa and wa.isChecked() and wb.isChecked():
                self._uncheck(wb)
            elif sender is wb and wb.isChecked() and wa.isChecked():
                self._uncheck(wa)
        self._refresh()

    @staticmethod
    def _uncheck(widget: QCheckBox):
        widget.blockSignals(True)
        widget.setChecked(False)
        widget.blockSignals(False)

    def _enforce_exclusive(self):
        """Clear any mutually exclusive pair that is set on both sides.

        _on_toggle only fires for interactive changes, so loading a profile
        bypasses it entirely. A profile saved before a pair was added to
        MUTUALLY_EXCLUSIVE can hold a combination FreeRDP refuses. The second
        of the pair is the one dropped, so the outcome does not depend on
        dictionary order.
        """
        for a, b in MUTUALLY_EXCLUSIVE:
            wa, wb = self.toggle_widgets[a], self.toggle_widgets[b]
            if wa.isChecked() and wb.isChecked():
                self._uncheck(wb)

    def _refresh(self):
        conn = self._connection()
        self.preview.setPlainText(" \\\n    ".join(conn.command()))
        host = clean_value(self.host_in.text())
        workspace = clean_value(self.workspace_in.text())
        # Either a host or a workspace file identifies the target; one is
        # enough to launch.
        blocking = (
            not is_usable(clean_value(self.bin_in.text()))
            or not (host or workspace)
        )
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
        if host and not workspace and self._dns_ok.get(host) is False:
            warnings.append(
                f"'{host}' does not resolve via DNS or /etc/hosts. "
                "It must match the Entra-registered device name exactly."
            )
        if warnings:
            self.warn_label.setText(" · ".join(warnings))
            self.warn_label.setStyleSheet(STATUS_COLORS["warn"])
        else:
            self.warn_label.clear()

        # No point resolving a host that will not be used.
        if host and not workspace and host not in self._dns_ok:
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

    def _on_workspace_changed(self):
        """Grey out the host field while a workspace file is selected.

        The file supplies the target, so an editable host box would imply
        it still matters. The text is left in place rather than cleared so
        clearing the workspace file restores the previous setup.
        """
        active = bool(clean_value(self.workspace_in.text()))
        self.host_in.setEnabled(not active)
        self._refresh()

    def _browse_workspace(self):
        current = clean_value(self.workspace_in.text())
        start = str(Path(current).parent) if current else str(
            Path.home() / "Downloads"
        )
        if not Path(start).is_dir():
            start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select AVD workspace file", start,
            "Remote Desktop files (*.rdpw *.rdp);;All files (*)",
        )
        if path:
            self.workspace_in.setText(path)

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
            # Only a stat, so this stays on the UI thread.
            self._set_label("Not found, or not executable.", "error")
        elif path in self._webview:
            self._show_webview(self._webview[path])
        else:
            self._set_label("Checking build flags...", "muted")
            self._bin_timer.start()
        self._refresh()

    def _start_webview_probe(self):
        path = clean_value(self.bin_in.text())
        if not path or not is_usable(path) or path in self._webview:
            return
        probe = WebviewProbe(path)
        probe.signals.finished.connect(self._webview_result)
        self._pool.start(probe)

    def _webview_result(self, path: str, state: Webview):
        self._webview[path] = state
        # Only report if the field still holds the path that was probed.
        if clean_value(self.bin_in.text()) == path:
            self._show_webview(state)

    def _show_webview(self, state: Webview):
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

    def _autodetect_binary(self, note: str = ""):
        """Fill the binary field from a background search.

        `note`, if given, is shown when the search succeeds and may contain a
        {path} placeholder.
        """
        if self._search_pending:
            return
        self._search_pending = True
        search = BinarySearch()
        search.signals.finished.connect(
            lambda path: self._detected_binary(path, note)
        )
        self._pool.start(search)

    def _detected_binary(self, path: str, note: str):
        self._search_pending = False
        # The search takes seconds, in which the user may have browsed to a
        # binary or loaded another profile. Never overwrite that.
        if not path or clean_value(self.bin_in.text()):
            return
        self.bin_in.setText(path)
        if note:
            self._set_status(note.format(path=path), "warn")

    def _set_label(self, text: str, kind: str):
        self.webview_label.setText(text)
        self.webview_label.setStyleSheet(STATUS_COLORS.get(kind, ""))

    # ------------------------------------------------------------ actions
    def _copy(self):
        QApplication.clipboard().setText(" ".join(self._connection().command()))
        self._set_status("Command copied to clipboard", "ok")

    def _connect(self):
        args = self._connection().command()
        # Output goes to an unlinked temporary file, not a pipe.
        #
        # _check below reads it once, 2.5s in, and then nobody reads it again.
        # With a pipe, FreeRDP's own logging fills the 64 KB buffer during a
        # normal session and the client blocks mid-write — the session freezes
        # with no indication why. A file never applies back-pressure, and it
        # still captures the output needed to explain an immediate exit.
        # Not a context manager: the handle has to outlive this scope, because
        # the child writes to it and _check reads it back 2.5s from now.
        log = tempfile.TemporaryFile(mode="w+", prefix="entrardp-", suffix=".log")  # noqa: SIM115
        try:
            proc = subprocess.Popen(
                args, start_new_session=True,
                stdout=log, stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log.close()
            QMessageBox.critical(self, "Launch failed", f"Could not start {args[0]}\n\n{exc}")
            return
        self._set_status("Starting session...", "muted")
        QTimer.singleShot(2500, lambda: self._check(proc, log))

    def _check(self, proc, log):
        if proc.poll() is None:
            self._set_status(f"Session running (pid {proc.pid})", "ok")
            # The child holds its own descriptor, so closing ours does not
            # disturb it. The file is already unlinked and disappears when the
            # session exits.
            log.close()
            return
        try:
            log.seek(0)
            output = log.read()
        except OSError:
            output = ""
        finally:
            log.close()
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

        # A stored binary path is a preference, not a guarantee. It goes stale
        # when the build is replaced, removed, or the profile is opened on
        # another machine, and a profile should not strand the user on a path
        # that no longer exists. Fall back to detection in that case.
        stored = data.get("binary", "")
        if stored and is_usable(stored):
            self.bin_in.setText(stored)
        else:
            # Detection probes every candidate binary, so it runs in the
            # background and fills the field when it completes.
            self.bin_in.clear()
            self._autodetect_binary(
                f"'{name}' pointed at a binary that is gone; using {{path}}"
                if stored else ""
            )
        for key, widget in self.toggle_widgets.items():
            widget.blockSignals(True)
            widget.setChecked(data.get("toggles", {}).get(key, widget.isChecked()))
            widget.blockSignals(False)
        self._enforce_exclusive()
        self.res_group.setChecked(data.get("manual_res", True))
        self.width_in.setValue(data.get("width", 2560))
        self.height_in.setValue(data.get("height", 1440))
        self.force_x11.setChecked(data.get("force_x11", True))
        self.extra_in.setText(data.get("extra", ""))
        # Absent from profiles written before workspace support existed.
        self.workspace_in.setText(data.get("workspace_file", ""))
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
            "workspace_file": clean_value(conn.workspace_file),
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
        # Checked before the delete: the last_used getter filters out names no
        # longer in the store, so afterwards it always reports None and the
        # state file would never actually be cleared.
        was_last = self.store.last_used == name
        self.store.delete(name)
        if was_last:
            self.store.last_used = None
        self.profile_box.setCurrentText("")
        self._reload_profiles()
        self.profile_box.setCurrentText("")
        self._set_status(f"Deleted '{name}'", "muted")
