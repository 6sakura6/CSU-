"""Qt-to-scheduler integration, using isolated storage and offline workers only."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app as legacy
from core import RunResult, now_cn
from glass_ui import Bridge, GlassApp, QtRoot, UI_DIR, run
from storage import Store
from test_tray import FakeTray
from test_startup import FakeStartup


class GlassUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)
        QQuickStyle.setStyle("Basic")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        self.bridge = Bridge()
        self.window = QQuickWindow()
        self.root = QtRoot(self.qt, self.window)
        self.app = GlassApp(self.root, self.bridge, self.store, FakeTray, FakeStartup)
        self.bridge.ready = True
        self.app.set_awake = MagicMock()
        self.bridge.publish()

    def tearDown(self):
        self.app.worker = None
        self.app.finish_close()
        for handler in list(self.app.logger.handlers):
            handler.close()
            self.app.logger.removeHandler(handler)
        self.window.deleteLater()
        self.qt.processEvents()
        self.directory.cleanup()

    def account(self):
        self.assertTrue(self.bridge.saveAccount("offline-qt-account", "offline-qt-password"))

    def test_account_is_encrypted_and_password_is_never_in_view_model(self):
        self.account()
        self.assertEqual(self.store.credentials()["username"], "offline-qt-account")
        self.assertNotIn("password", self.bridge.state)
        self.assertNotIn("offline-qt-password", str(self.bridge.state))
        self.assertNotIn(b"offline-qt-password", (self.store.root / "credentials.dat").read_bytes())

    def test_changing_account_clears_old_completion_and_saved_session(self):
        self.account()
        self.store.save_secret("session.dat", {"cookies": ["offline"]})
        self.app.completed_day = now_cn().date().isoformat()
        self.assertTrue(self.bridge.saveAccount("different-qt-account", "different-test-password"))
        self.assertFalse(self.bridge.state["completed"])
        self.assertIsNone(self.store.load_secret("session.dat"))

    def test_invalid_time_does_not_change_settings_or_schedule(self):
        before = self.app.settings
        with patch.object(self.app.dialogs, "showerror") as error:
            self.assertFalse(self.bridge.saveSchedule("25:00", "23:25", 5))
        error.assert_called_once()
        self.assertIs(self.app.settings, before)

    def test_schedule_can_be_configured_before_account_and_persists(self):
        self.assertTrue(self.bridge.saveSchedule("20:20", "23:00", 7))
        self.assertEqual(self.store.settings().start, "20:20")
        self.assertEqual(self.bridge.state["retry"], "7")
        self.assertFalse(self.bridge.state["enabled"])

    def test_location_validates_converts_and_rejects_out_of_range(self):
        self.assertTrue(self.bridge.saveLocation(True, '30°15′30″', '120°30′30″'))
        self.assertAlmostEqual(self.store.manual_location().latitude, 30.25833333)
        with patch.object(self.app.dialogs, "showerror"):
            self.assertFalse(self.bridge.saveLocation(True, "120", "30"))
        self.assertAlmostEqual(self.store.manual_location().latitude, 30.25833333)
        self.assertTrue(self.bridge.state["locationManual"])

    def test_network_credentials_are_independent_and_empty_password_is_preserved(self):
        self.account()
        self.assertTrue(self.bridge.saveNetwork(True, "network-qt-user", "network-test-password", 0, False))
        self.assertTrue(self.bridge.saveNetwork(True, "network-qt-user", "", 2, False))
        secret = self.store.load_secret("network.dat")
        self.assertEqual(secret["password"], "network-test-password")
        self.assertEqual(secret["carrier"], "unicom")
        self.assertEqual(self.store.credentials()["username"], "offline-qt-account")

    def test_corrupt_network_secret_can_be_disabled(self):
        (self.store.root / "network.dat").write_bytes(b"corrupt")
        self.assertTrue(self.bridge.saveNetwork(False, "", "", -1, False))
        self.assertFalse(self.store.settings().campus_network_enabled)

    def test_finished_worker_stays_locked_until_result_is_consumed(self):
        self.app.worker = MagicMock()
        self.app.worker.is_alive.return_value = False
        self.bridge.publish()
        self.assertTrue(self.bridge.state["busy"])
        self.assertFalse(self.bridge.state["canEdit"])
        self.assertFalse(self.bridge.saveAccount("blocked", "offline-test-password"))

    def test_check_button_routes_to_existing_dry_run_and_does_not_mark_done(self):
        self.account()
        with patch("app.BrowserRunner") as runner:
            runner.return_value.run.return_value = RunResult("checked", "Offline check complete")
            self.bridge.runTask("check")
            self.app.worker.join(3)
            self.app.tick()
            runner.return_value.run.assert_called_once_with(dry_run=True)
        self.assertFalse(self.store.done(now_cn().date().isoformat()))
        self.assertFalse(self.bridge.state["completed"])

    def test_stop_manual_operation_requests_cancellation_without_starting_timer(self):
        self.app.worker = MagicMock()
        self.app.worker.is_alive.return_value = True
        self.bridge.toggleSchedule()
        self.assertTrue(self.app.stop_event.is_set())
        self.assertFalse(self.app.enabled)

    def test_settings_pause_new_work_and_logs_do_not(self):
        self.bridge.openPanel("logs")
        self.assertFalse(self.app.network_dialog_open)
        self.bridge.panelClosed()
        self.bridge.openPanel("network")
        self.assertTrue(self.app.network_dialog_open)
        self.bridge.panelClosed()
        self.assertFalse(self.app.network_dialog_open)

    def test_disclaimer_decline_never_initializes_live_scheduler(self):
        with patch("glass_ui.QtDialogs.askokcancel", return_value=False), patch("glass_ui.GlassApp") as scheduler:
            self.assertEqual(run(), 0)
        scheduler.assert_not_called()

    def test_default_entry_routes_to_qt_after_single_instance_check(self):
        with patch.object(sys, "argv", ["app"]), patch("app.SingleInstance.signal", return_value=False), \
                patch("glass_ui.run", return_value=0) as qt_run:
            self.assertEqual(legacy.main(), 0)
        qt_run.assert_called_once_with(from_login=False)

    def test_qml_loads_without_warnings_and_primary_action_is_connected(self):
        self.account()
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda values: warnings.extend(item.toString() for item in values))
        engine.rootContext().setContextProperty("bridge", self.bridge)
        engine.load(QUrl.fromLocalFile(str(UI_DIR / "Main.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        window.setWidth(1488)
        window.setHeight(1056)
        self.qt.processEvents()
        button = window.findChild(QObject, "runButton")
        self.assertIsNotNone(button)
        with patch.object(self.app, "launch") as launch:
            button.clicked.emit()
        launch.assert_called_once_with("manual")
        self.assertFalse(warnings, warnings)
        engine.deleteLater()
        self.qt.processEvents()

    def test_responsive_controls_stay_inside_window_and_clear_of_execution_time(self):
        self.account()
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", self.bridge)
        engine.load(QUrl.fromLocalFile(str(UI_DIR / "Main.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        window.show()
        for width in (880, 1000, 1200, 1488):
            window.setWidth(width)
            window.setHeight(800)
            QTest.qWait(60)  # Allow a real scene-graph/layout frame after resize.
            for name in ("runButton", "checkButton", "scheduleButton", "locationShortcut", "networkShortcut", "startupSwitch"):
                item = window.findChild(QObject, name)
                start = item.mapToScene(QPointF(0, 0))
                self.assertGreaterEqual(start.x(), 0, (width, name))
                self.assertLessEqual(start.x() + item.width(), width, (width, name))
            action = window.findChild(QObject, "scheduleButton")
            execution = window.findChild(QObject, "nextExecution")
            self.assertLess(action.mapToScene(QPointF(action.width(), 0)).x(),
                            execution.mapToScene(QPointF(0, 0)).x(), width)
        window.hide()
        engine.deleteLater()
        self.qt.processEvents()


if __name__ == "__main__":
    unittest.main()
