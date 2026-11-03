"""WinForms window clipping and tray, using the host's existing .NET runtime."""
from pathlib import Path
import threading


class NativeWindow:
    def __init__(self, window, icon_path, close, toggle_schedule=None):
        self.window, self.icon_path, self.close = window, str(icon_path), close
        self.tray = None
        self.toggle_schedule = toggle_schedule
        self.running = None
        self.native_hooks = []
        self.resize_grips = []

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
            from System.Windows.Forms import ContextMenuStrip, NotifyIcon, Panel, Cursors, ControlStyles
            class ResizeGrip(Panel):
                __namespace__='CSUCheckinLite'
                def __init__(self):
                    super().__init__()
                    self.SetStyle(ControlStyles.Opaque | ControlStyles.UserPaint,True)
            for edge,cursor in [('left',Cursors.SizeWE),('right',Cursors.SizeWE),
                    ('top',Cursors.SizeNS),('bottom',Cursors.SizeNS),
                    ('top-left',Cursors.SizeNWSE),('bottom-right',Cursors.SizeNWSE),
                    ('top-right',Cursors.SizeNESW),('bottom-left',Cursors.SizeNESW)]:
                grip=ResizeGrip()
                grip.Cursor=cursor
                def mouse_down(_,event,edge=edge):
                    from System.Windows.Forms import MouseButtons
                    if event.Button == MouseButtons.Left:
                        self.start_resize(edge)
                grip.MouseDown += mouse_down
                self.native_hooks.append(mouse_down)
                self.window.native.Controls.Add(grip)
                # Paint after the WebView sibling, without drawing a background of our own.
                import ctypes
                from ctypes import wintypes
                user=ctypes.WinDLL('user32')
                user.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]
                user.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,wintypes.LONG]
                handle=int(grip.Handle.ToInt64())
                user.SetWindowLongW(handle,-20,user.GetWindowLongW(handle,-20)|0x20)
                self.resize_grips.append((edge,grip))
            self.window.native.SizeChanged += self._resize
            self._resize()
            menu = ContextMenuStrip()
            show = menu.Items.Add('显示主窗口')
            show.Click += lambda *_: self.show()
            self.schedule_item = menu.Items.Add('开始定时')
            if self.toggle_schedule:
                self.schedule_item.Click += lambda *_: threading.Thread(target=self.toggle_schedule, daemon=True).start()
            quit_item = menu.Items.Add('退出程序')
            quit_item.Click += lambda *_: threading.Thread(target=self.close, daemon=True).start()
            self.tray = NotifyIcon()
            self.tray.Icon = Icon(self.icon_path)
            self.tray.Text = '平安打卡助手'
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
        width, height = form.ClientSize.Width, form.ClientSize.Height
        border=max(6,int(12*form.DeviceDpi/96))
        corner=max(border,int(20*form.DeviceDpi/96))
        bounds={'left':(0,corner,border,max(0,height-2*corner)),
                'right':(width-border,corner,border,max(0,height-2*corner)),
                'top':(corner,0,max(0,width-2*corner),border),
                'bottom':(corner,height-border,max(0,width-2*corner),border),
                'top-left':(0,0,corner,corner),'top-right':(width-corner,0,corner,corner),
                'bottom-left':(0,height-corner,corner,corner),
                'bottom-right':(width-corner,height-corner,corner,corner)}
        for edge,grip in self.resize_grips:
            grip.SetBounds(*bounds[edge])
            grip.Visible=form.WindowState == FormWindowState.Normal
            grip.BringToFront()
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
        def restore():
            from System.Windows.Forms import FormWindowState
            form = self.window.native
            form.Show()
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            form.Activate()
        self.invoke(restore)

    def toggle_maximize(self):
        def toggle():
            from System.Windows.Forms import FormWindowState, Screen
            form = self.window.native
            form.MaximizedBounds = Screen.FromHandle(form.Handle).WorkingArea
            form.WindowState = FormWindowState.Normal if form.WindowState == FormWindowState.Maximized else FormWindowState.Maximized
        self.invoke(toggle)

    def start_resize(self, edge):
        codes={'left':10,'right':11,'top':12,'top-left':13,'top-right':14,
               'bottom':15,'bottom-left':16,'bottom-right':17}
        if edge not in codes:
            raise ValueError('窗口边缘无效。')
        def resize():
            import ctypes
            from ctypes import wintypes
            from System.Windows.Forms import FormWindowState
            if self.window.native.WindowState != FormWindowState.Normal:
                return
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.ReleaseCapture.argtypes=[]
            user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            user.SendMessageW.restype=wintypes.LPARAM
            point=wintypes.POINT()
            user.GetCursorPos(ctypes.byref(point))
            position=(point.x & 0xffff)|((point.y & 0xffff)<<16)
            user.ReleaseCapture()
            user.SendMessageW(int(self.window.native.Handle.ToInt64()),0x00A1,codes[edge],position)
        self.invoke(resize)

    def set_running(self, enabled):
        if self.tray is None or self.running == enabled:
            return
        self.running = enabled
        def update():
            self.schedule_item.Text = '暂停定时' if enabled else '开始定时'
            self.tray.Text = '平安打卡助手 · ' + ('定时已启动' if enabled else '定时未启动')
        self.invoke(update)

    def restrict_navigation(self, page_url):
        from urllib.parse import urlsplit
        expected = urlsplit(page_url)
        def navigation(_, event):
            candidate = urlsplit(str(event.Uri))
            if candidate.scheme != expected.scheme or candidate.netloc != expected.netloc or candidate.path != expected.path:
                event.Cancel = True
        def popup(_, event):
            event.Handled = True
        def attach():
            core = self.window.native.webview.CoreWebView2
            core.NavigationStarting += navigation
            core.NewWindowRequested += popup
            core.Settings.AreDevToolsEnabled = False
            core.Settings.AreDefaultContextMenusEnabled = False
        self.native_hooks.extend([navigation, popup])
        self.invoke(attach)

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
                'native_resize_grips': len(self.resize_grips),
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
