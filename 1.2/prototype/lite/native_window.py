"""WinForms window clipping and tray, using the host's existing .NET runtime."""
from pathlib import Path
import threading


class NativeWindow:
    def __init__(self, window, icon_path, close):
        self.window, self.icon_path, self.close = window, str(icon_path), close
        self.tray = None

    def invoke(self, function):
        from System import Action
        form = self.window.native
        if form.InvokeRequired:
            form.Invoke(Action(function))
        else:
            function()

    def attach(self):
        def setup():
            from System.Drawing import Icon
            from System.Windows.Forms import ContextMenuStrip, NotifyIcon
            self.window.native.SizeChanged += self._resize
            self._resize()
            menu = ContextMenuStrip()
            show = menu.Items.Add('显示轻量验证版')
            show.Click += lambda *_: self.show()
            quit_item = menu.Items.Add('退出验证版')
            quit_item.Click += lambda *_: threading.Thread(target=self.close, daemon=True).start()
            self.tray = NotifyIcon()
            self.tray.Icon = Icon(self.icon_path)
            self.tray.Text = '平安打卡 · 轻量验证版'
            self.tray.ContextMenuStrip = menu
            self.tray.DoubleClick += lambda *_: self.show()
            self.tray.Visible = True
        self.invoke(setup)

    def _resize(self, *_):
        from System.Drawing import Region
        from System.Drawing.Drawing2D import GraphicsPath
        from System.Windows.Forms import FormWindowState
        form = self.window.native
        previous = form.Region
        if form.WindowState == FormWindowState.Maximized:
            form.Region = None
        else:
            width, height = form.ClientSize.Width, form.ClientSize.Height
            diameter = max(2, int(48 * form.DeviceDpi / 96))
            shape = GraphicsPath()
            for x, y, start in [(0, 0, 180), (width-diameter, 0, 270),
                                (width-diameter, height-diameter, 0), (0, height-diameter, 90)]:
                shape.AddArc(x, y, diameter, diameter, start, 90)
            shape.CloseFigure()
            form.Region = Region(shape)
            shape.Dispose()
        if previous is not None:
            previous.Dispose()

    def show(self):
        self.window.show()
        self.window.restore()

    def hide(self):
        if self.tray is not None and self.tray.Visible:
            self.window.hide()
            return True
        return False

    def info(self):
        state = {}
        def inspect():
            form = self.window.native
            region = form.Region
            state.update({'tray': bool(self.tray and self.tray.Visible),
                'rounded': bool(region is not None and not region.IsVisible(0, 0)
                    and region.IsVisible(form.Width // 2, form.Height // 2)),
                'width': int(form.Width), 'height': int(form.Height),
                'webview_version': str(form.webview.CoreWebView2.Environment.BrowserVersionString)})
        self.invoke(inspect)
        return state

    def capture(self, filename):
        """Export the rendered WebView and the actual native window region to PNG."""
        from System.Drawing import Bitmap, Color, Graphics, Image
        from System.Drawing.Imaging import ImageFormat, PixelFormat
        from System.IO import FileMode, FileStream
        from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
        path = Path(filename).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_path = str(path.with_suffix('.webview.png'))
        stream = FileStream(raw_path, FileMode.Create)
        holder = {}
        try:
            self.invoke(lambda: holder.update(task=self.window.native.webview.CoreWebView2.CapturePreviewAsync(
                CoreWebView2CapturePreviewImageFormat.Png, stream)))
            if not holder['task'].Wait(15000):
                raise TimeoutError('界面截图超时。')
        finally:
            stream.Dispose()
        source = Image.FromFile(raw_path)
        bitmap = Bitmap(source.Width, source.Height, PixelFormat.Format32bppArgb)
        graphic = Graphics.FromImage(bitmap)
        try:
            graphic.Clear(Color.Transparent)
            def apply_region():
                region = self.window.native.Region
                if region is not None:
                    graphic.Clip = region
                graphic.DrawImage(source, 0, 0, source.Width, source.Height)
            self.invoke(apply_region)
            bitmap.Save(str(path), ImageFormat.Png)
        finally:
            graphic.Dispose()
            bitmap.Dispose()
            source.Dispose()
            Path(raw_path).unlink(missing_ok=True)

    def dispose(self):
        if self.tray:
            def cleanup():
                self.tray.Visible = False
                self.tray.Dispose()
            self.invoke(cleanup)
