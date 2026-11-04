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

    def center_on_screen(self):
        """Fit and center the native window before its first display."""
        def center():
            from System.Drawing import Point, Size
            from System.Windows.Forms import FormStartPosition, FormWindowState, Screen
            form = self.window.native
            if form.WindowState != FormWindowState.Normal:
                return
            area = Screen.FromHandle(form.Handle).WorkingArea
            form.StartPosition = FormStartPosition.Manual
            form.MinimumSize = Size(min(form.MinimumSize.Width, area.Width),
                                    min(form.MinimumSize.Height, area.Height))
            form.Size = Size(min(form.Width, area.Width), min(form.Height, area.Height))
            form.Location = Point(area.Left + (area.Width - form.Width) // 2,
                                  area.Top + (area.Height - form.Height) // 2)
        self.invoke(center)

    def attach(self):
        def setup():
            import ctypes
            from ctypes import wintypes
            from System.Drawing import Icon, Color
            from System.Windows.Forms import ContextMenuStrip, NotifyIcon, Panel, Cursors
            self.user=ctypes.WinDLL('user32',use_last_error=True)
            self.user.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]
            self.user.GetWindowLongW.restype=wintypes.LONG
            self.user.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,wintypes.LONG]
            self.user.SetWindowLongW.restype=wintypes.LONG
            self.user.SetLayeredWindowAttributes.argtypes=[wintypes.HWND,wintypes.DWORD,wintypes.BYTE,wintypes.DWORD]
            self.user.SetLayeredWindowAttributes.restype=wintypes.BOOL
            self.user.GetLayeredWindowAttributes.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.BYTE),ctypes.POINTER(wintypes.DWORD)]
            self.user.GetLayeredWindowAttributes.restype=wintypes.BOOL
            for edge,cursor in [('left',Cursors.SizeWE),('right',Cursors.SizeWE),
                    ('top',Cursors.SizeNS),('bottom',Cursors.SizeNS),
                    ('top-left',Cursors.SizeNWSE),('bottom-right',Cursors.SizeNWSE),
                    ('top-right',Cursors.SizeNESW),('bottom-left',Cursors.SizeNESW)]:
                grip=Panel()
                grip.Visible=False
                grip.TabStop=False
                grip.BackColor=Color.White
                grip.Cursor=cursor
                def mouse_down(_,event,edge=edge):
                    from System.Windows.Forms import MouseButtons
                    if event.Button == MouseButtons.Left:
                        self.start_resize(edge)
                grip.MouseDown += mouse_down
                self.native_hooks.append(mouse_down)
                self.window.native.Controls.Add(grip)
                handle=int(grip.Handle.ToInt64())
                # WS_EX_TRANSPARENT only changes sibling paint order; WebView2
                # content is composited separately and can leave rectangular
                # stale pixels. Give each hit target a real layered surface.
                # A nonzero 1/255 alpha retains native mouse hit testing; zero
                # alpha or WS_EX_TRANSPARENT would pass the resize clicks through.
                style=self.user.GetWindowLongW(handle,-20)
                ctypes.set_last_error(0)
                previous=self.user.SetWindowLongW(handle,-20,(style|0x80000)&~0x20)
                if previous==0 and ctypes.get_last_error():
                    raise ctypes.WinError(ctypes.get_last_error())
                if not self.user.SetLayeredWindowAttributes(handle,0,1,2):
                    raise ctypes.WinError(ctypes.get_last_error())
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
        from System.Drawing.Drawing2D import GraphicsPath, Matrix
        from System.Windows.Forms import FormWindowState
        form = self.window.native
        previous = form.Region
        width, height = form.Width, form.Height
        border=max(6,int(12*form.DeviceDpi/96))
        corner=max(border,int(20*form.DeviceDpi/96))
        bounds={'left':(0,corner,border,max(0,height-2*corner)),
                'right':(width-border,corner,border,max(0,height-2*corner)),
                'top':(corner,0,max(0,width-2*corner),border),
                'bottom':(corner,height-border,max(0,width-2*corner),border),
                'top-left':(0,0,corner,corner),'top-right':(width-corner,0,corner,corner),
                'bottom-left':(0,height-corner,corner,corner),
                'bottom-right':(width-corner,height-corner,corner,corner)}
        if form.WindowState == FormWindowState.Maximized:
            form.Region = None
        else:
            diameter = max(2, int(48 * form.DeviceDpi / 96))
            shape = GraphicsPath()
            for x, y, start in [(0, 0, 180), (width-diameter, 0, 270),
                                (width-diameter, height-diameter, 0), (0, height-diameter, 90)]:
                shape.AddArc(x, y, diameter, diameter, start, 90)
            shape.CloseFigure()
            form.Region = Region(shape)
            shape.Dispose()
        for edge,grip in self.resize_grips:
            grip.SetBounds(*bounds[edge])
            old_clip=grip.Region
            if form.Region is None:
                grip.Region=None
            else:
                clip=form.Region.Clone()
                transform=Matrix()
                transform.Translate(-grip.Left,-grip.Top)
                clip.Transform(transform)
                transform.Dispose()
                grip.Region=clip
            if old_clip is not None:
                old_clip.Dispose()
            grip.Visible=form.WindowState == FormWindowState.Normal
            grip.BringToFront()
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
            import ctypes
            from ctypes import wintypes
            form = self.window.native
            region = form.Region
            corners=[(0,0),(form.Width-1,0),(0,form.Height-1),(form.Width-1,form.Height-1)]
            opacity=[]
            clipped=[]
            for edge,grip in self.resize_grips:
                alpha,flags,key=wintypes.BYTE(),wintypes.DWORD(),wintypes.DWORD()
                handle=int(grip.Handle.ToInt64())
                ok=self.user.GetLayeredWindowAttributes(handle,ctypes.byref(key),ctypes.byref(alpha),ctypes.byref(flags))
                style=self.user.GetWindowLongW(handle,-20)
                opacity.append(bool(ok and alpha.value==1 and flags.value&2 and style&0x80000 and not style&0x20))
                if edge in ('top-left','top-right','bottom-left','bottom-right'):
                    x=grip.Width-1 if edge.endswith('right') else 0
                    y=grip.Height-1 if edge.startswith('bottom') else 0
                    clipped.append(bool(grip.Region is not None and not grip.Region.IsVisible(x,y)))
            state.update({'tray': bool(self.tray and self.tray.Visible),
                'rounded': bool(region is not None and all(not region.IsVisible(x,y) for x,y in corners)
                    and region.IsVisible(form.Width // 2, form.Height // 2)),
                'native_resize_grips': len(self.resize_grips),
                'resize_grips_composited': bool(opacity and all(opacity)),
                'resize_corners_clipped': bool(clipped and all(clipped)),
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
