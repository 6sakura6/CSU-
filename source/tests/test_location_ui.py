"""Local Tk settings checks. Only temporary data and offline accounts are used."""
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from storage import Store


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class LocationUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.store = Store(Path(self.directory.name))
        self.app = App(self.root, self.store)
        self.root.update_idletasks()

    def tearDown(self):
        for timer in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(timer)
        self.app.finish_close()
        for handler in list(self.app.logger.handlers):
            handler.close()
            self.app.logger.removeHandler(handler)
        self.directory.cleanup()

    def dialog_action(self, action):
        errors = []
        def interact():
            dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
            try:
                dialog.update_idletasks()
                action(dialog)
            except Exception as error:
                errors.append(error)
                dialog.destroy()
        timer = self.root.after(60, interact)
        self.app.configure_location()
        if errors:
            raise errors[0]

    def test_manual_coordinate_dialog_validates_saves_and_fits(self):
        def action(dialog):
            items = list(descendants(dialog))
            manual = next(item for item in items if isinstance(item, ttk.Radiobutton) and item.cget('value') == 'manual')
            manual.invoke()
            entries = [item for item in items if isinstance(item, ttk.Entry)]
            entries[0].insert(0, '30°15′30″')
            entries[1].insert(0, '120°30′30″')
            buttons = {item.cget('text'): item for item in items if isinstance(item, ttk.Button)}
            buttons['校验并换算'].invoke()
            dialog.update_idletasks()
            labels = [item for item in items if isinstance(item, ttk.Label)]
            self.assertTrue(any('30.25833333' in item.cget('text') and '120.50833333' in item.cget('text') for item in labels))
            for item in labels + list(buttons.values()) + entries:
                self.assertTrue(item.winfo_ismapped())
                self.assertLessEqual(item.winfo_rooty() + item.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())
                self.assertLessEqual(item.winfo_rootx() + item.winfo_width(), dialog.winfo_rootx() + dialog.winfo_width())
            buttons['保存定位设置'].invoke()
        with patch('app.messagebox.showerror') as error:
            self.dialog_action(action)
            error.assert_not_called()
        self.assertEqual(self.app.settings.location_mode, 'manual')
        self.assertAlmostEqual(self.store.manual_location().latitude, 30 + 15/60 + 30/3600)
        self.assertIn('手动输入', self.app.location_text.get())
        self.app.username.set('offline-ui-account')
        self.app.password.set('offline-ui-password')
        self.assertTrue(self.app.save(quiet=True))
        self.assertEqual(self.store.settings().location_mode, 'manual')

    def test_invalid_coordinate_does_not_save(self):
        def action(dialog):
            items = list(descendants(dialog))
            next(item for item in items if isinstance(item, ttk.Radiobutton) and item.cget('value') == 'manual').invoke()
            entries = [item for item in items if isinstance(item, ttk.Entry)]
            entries[0].insert(0, '120')
            entries[1].insert(0, '30')
            buttons = {item.cget('text'): item for item in items if isinstance(item, ttk.Button)}
            buttons['保存定位设置'].invoke()
            self.assertTrue(dialog.winfo_exists())
            self.assertEqual(self.app.settings.location_mode, 'device')
            buttons['取消'].invoke()
        with patch('app.messagebox.showerror') as error:
            self.dialog_action(action)
            error.assert_called_once()
        self.assertIsNone(self.store.manual_location())

    def test_cancel_preserves_device_mode(self):
        def action(dialog):
            next(item for item in descendants(dialog) if isinstance(item, ttk.Button) and item.cget('text') == '取消').invoke()
        self.dialog_action(action)
        self.assertEqual(self.app.settings.location_mode, 'device')
        self.assertIsNone(self.store.manual_location())
