# 轻毛玻璃界面

实现用户选定的第一张效果图：状态与执行时间置顶，账户和定时双栏，快捷设置和运行记录置底。窗口小于 1280px 时重排顶部，小于 1000px 时设置和快捷入口改为单栏，内容可滚动。

界面为 PySide6 / Qt Quick，调度、打卡、凭据加密和托盘沿用 `app.App`。`QtRoot`、可观察值和动作状态适配既有控制器，UI 不复制打卡业务。旧 Tk 界面可通过 `python source/app.py --legacy-ui` 运行。

1.1.1 将最外层窗口改为 24px 圆角，通过透明窗口和整体圆角遮罩裁切标题栏及内容，弹层遮罩同步使用该圆角。最大化/全屏时半径为 0，恢复窗口后重新使用圆角。

动效包含按钮按压、开关滑动、弹层进入退出、提示条淡入淡出和执行中的状态环；读取 Windows 客户区动画偏好，`CHECKIN_REDUCED_MOTION=1` 也可禁用动效。背景始终为应用内部素材，毛玻璃通过采样该背景、模糊、圆角遮罩和乳白表面叠加实现，不读取桌面或其他窗口。

## 安全预览

```powershell
python source/app.py --design-preview
python source/app.py --design-preview --preview-size 1488x1056 --capture work/ui-preview/main.png
```

预览用临时目录、假账户、模拟任务，不访问学校服务，不注册开机启动，不读取或更改现有 `data/`。预览右下角有示例数据标识。

## 素材

- `assets/background.png`：使用内置 Image Gen 生成的背景，参考第一版效果图；提示词为“去除全部界面、文字、图标和面板，仅保留 1440×1024 的浅冰蓝、乳白及轻微淡紫的完全失焦背景，低饱和、高亮度、无纹理和装饰”。没有将效果图整体用作程序界面。
- 图标：Microsoft Fluent System Icons，MIT，原始路径 `https://github.com/microsoft/fluentui-system-icons/tree/main/assets`；Wi-Fi 图标来自同项目 `@fluentui/svg-icons@1.1.316`。保留原始 SVG，由桥接层着色；许可证见 `assets/Fluent-Icons-LICENSE.txt`。
- 字体：Windows 自带 Microsoft YaHei UI 与 Segoe UI，不额外分发字体。
