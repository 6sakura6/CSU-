# 轻量验证版 0.2.0（业务后端迁移）

此目录保留早期验证程序。完整轻量版已在 `source/lite_app.py` 实现，正常使用和构建请参阅根目录 README；以下内容记录 0.2.0 验证版的范围。

这是独立的技术验证程序，用于实测 WebView2 界面与轻量浏览器驱动的体积和基础能力。0.2.0 已包含不依赖界面框架的业务服务和完整网页流程的 CDP 驱动；**完整操作界面尚未接入，真实学校环境尚未验证，不能替代正式版。**

解压完整目录，运行 `CSUCheckinLitePrototype.exe`。点击「运行完整验证」会使用本机测试网页，实际填写预设表单、捕获响应、读取本地测试 Cookie、验证定位协议和新页面会话复用；全部完成后清理独立浏览器实例及其临时目录。

- 圆角外框、毛玻璃卡片、按钮和弹层动效；最大化时填满，还原后恢复圆角；支持系统减少动画设置。
- 设置会使用现有正式版的 Windows DPAPI 存储函数，保存到本验证版的独立目录；主界面不回显验证文字。
- 默认数据目录：`%LOCALAPPDATA%\CSUCheckinLitePrototype\validation-data`。不会读取正式版数据。
- 定时开关提供一次 5 秒倒计时，用于验证任务触发；原有每日排期、持久随机延迟和账户隔离规则通过独立用例验证。
- 右上角关闭按钮隐藏到系统托盘；双击托盘图标或右键「显示轻量验证版」恢复；底部或托盘菜单可退出。
- 当前演示界面和自检不连接学校、不读取真实账号、不提交打卡、不修改开机启动。
- 「定位协议」验证使用预设的本地测试坐标，不代表已验证电脑真实定位或学校定位流程。

## 系统依赖与体积口径

Windows 10/11 64 位、.NET Framework 4.8 或更新版本、Microsoft Edge WebView2 Runtime；浏览器能力检查还需安装 Edge。自动自检也会检查本机 Chrome（存在时）。WebView2 与 Edge 浏览器是两个独立组件。

使用系统共享 WebView2，不捆绑浏览器引擎、不自动下载安装运行库。系统缺少 Runtime 时需单独安装；其下载和磁盘占用不能算作零。初次解压后的整个应用目录（包括 `_internal`、资源、文档和体积报告）应小于 100,000,000 字节。

`size-report.json` 记录整个应用目录的实测值。验证产生的账号设置、临时页面、共享 Runtime 以及 WebView2 缓存须另行记录，不以移动目录来声称这些占用消失。退出时会清理当前验证会话的 WebView2 临时缓存和浏览器临时目录。

## 源码运行与构建

在项目根目录执行：

```powershell
python -m venv ../work/lite-venv
../work/lite-venv/Scripts/python.exe -m pip install -r prototype/lite/requirements.txt
../work/lite-venv/Scripts/python.exe prototype/lite/main.py
./prototype/lite/build.ps1
```

构建使用隔离依赖，不收集 Qt、Playwright、Node、NumPy、Pillow 和 Tk。原有 `source/core.py`、`source/storage.py` 与 `source/campus_network.py` 直接复用，旧界面与新业务服务共用抽出的排期规则及网页业务流程。

## 自动验证

```powershell
../work/lite-venv/Scripts/python.exe -m unittest discover -s prototype/lite/tests -v
../work/lite-venv/Scripts/python.exe prototype/lite/main.py --self-test --report work/lite-check.json --capture work/lite-main.png --data-root work/lite-test-data
../work/lite-venv/Scripts/python.exe source/lite_cli.py --self-test --report work/lite-backend-check.json
```

打包后的 EXE 支持同样的 `--self-test`、`--report`、`--capture` 和 `--data-root` 参数。自检覆盖原有加密与排期、JS/Python 桥接、本地 Edge/Chrome 登录与响应、测试定位、会话复用、托盘、圆角、缩放及设置弹层。

新增 `CSUCheckinLitePrototype.exe --backend-self-test --report <结果文件路径>`，在不打开界面的情况下，使用新业务控制器和轻量驱动完整验证：只检查、会话恢复、模拟提交后复核、次日排期及重复执行跳过。默认检查 Edge 和 Chrome，可用 `--channel edge` 或 `--channel chrome` 指定。当前主窗口仍用于基础能力演示，完整业务操作界面在下一阶段接入。

## 资源与许可

背景与 Fluent 图标复用正式版素材，图标 MIT 许可随素材分发。Python、pywebview、pythonnet、websocket-client 等组件保留各自许可。WebView2 SDK 由 pywebview 附带，系统 Runtime 由微软分发。此验证程序未打包正式版账号或学校会话。
