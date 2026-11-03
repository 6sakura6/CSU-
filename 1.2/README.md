# 平安校园打卡 · 1.2 系列

本文命令均在 `1.2` 目录执行。已有共享开发环境可使用 `../work/lite-venv/Scripts/python.exe`，或自行在本目录安装依赖。版本总索引见 [根目录说明](../README.md)。

面向中南大学智慧学工“平安打卡”的 Windows 桌面助手。支持校园账户登录、本地加密保存、电脑定位、每日定时执行和打卡结果复核。

**[下载 Windows 程序包](https://github.com/6sakura6/CSU-/releases/latest)** · [详细使用说明](使用说明.md) · [验证记录](验证记录.md)

当前源码为 1.2.2，修复了外边框角块，详见 [圆角修复说明](圆角修复说明-v1.2.2.md)。

## 使用

1. 从 Release 下载 ZIP，解压整个文件夹，运行 `平安打卡助手.exe`。保留同目录的 `_internal` 文件夹。每次打开均需阅读并同意免责声明后进入主界面；本程序仅供学习和测试使用，请在下载后 24 小时内删除。
2. 点击“校园账户”填写并保存账户密码，点击“每日定时”的时间行设置并保存执行时间。
3. 先运行“检查登录与定位”，确认可用后点击“开始定时”。开启后显示下次执行时间，按钮切换为“暂停定时”；执行过程中可点击“停止操作”。默认每天 21:00 检查，失败后每 5 分钟重试，至 23:25 截止。时间均为北京时间。

1.2 轻量版使用系统共享 WebView2 绘制圆角毛玻璃界面，使用本机 Edge / Chrome 执行正常网页流程。无需安装 Python，也不携带 Qt、Node、Playwright 或独立 Chromium 引擎。电脑需安装 WebView2 Runtime、.NET Framework 4.8 及 Edge 或 Chrome；执行时保持联网和程序运行。验证码或二次认证需要在浏览器中人工处理。

升级时先退出旧版，从新界面底部选择“导入旧版数据”，选择旧版的 `data` 文件夹。程序验证加密文件后导入，保留原配置的加密备份，并暂停打卡定时供核对。原 Windows 用户绑定关系不变，自启动沿用新目录的设置；若旧目录开启过自启动，请先在旧版关闭，再为新版启用。

1.2.1 修复了大厅跳转统一认证页后停住、未继续自动填写账号密码的问题。导航等待跟随当前主页面，离线自检也加入同类跳转。

## 功能

- 从服务大厅本次返回的菜单数据中，按“平安打卡”取得当前入口，支持菜单中的页面地址变化。
- 使用网页正常登录、定位和提交流程，并读取当天班次与学校开放时段。
- 当天确认完成后，为次日生成并加密保存一次 0–60 分钟随机延迟，叠加到每日基准时间；重启及失败重试沿用原排期。
- 已打卡或免打卡时跳过；提交后重新查询当天记录，确认结果。
- 账户密码与登录状态使用 Windows DPAPI 加密，绑定本机当前 Windows 用户。
- 可选的每小时校园网自动检测与认证，独立于打卡时段和打卡定时开关；区分无需运营商、移动、联通和电信，凭据独立加密保存，提供不提交打卡的连接测试。
- 未完成的任务按设定间隔重试，直到完成或超过当日截止时间；验证码等人工验证需要在浏览器中处理。可主动暂停定时和停止当前操作。

程序不会自动识别验证码，也不提供虚构定位。首次使用仍需验证真实账户、电脑定位及学校当前规则是否可用。

## 源码运行

1.2 轻量版使用本地 WebView2 界面，包含 24px 圆角外框、圆角面板、开关和弹层动效。最大化时贴合屏幕，还原后恢复圆角。Windows 关闭动画效果时，程序也会减少动效。打卡、调度、加密存储和独立校园网逻辑继续复用已有规则。

环境：Windows 10 / 11 64 位、Python 3.11、Microsoft Edge 或 Google Chrome。

```powershell
python -m venv work/lite-venv
work/lite-venv/Scripts/python.exe -m pip install -r source/requirements.txt
work/lite-venv/Scripts/python.exe source/lite_app.py
```

可用 `python source/lite_app.py --design-preview` 打开使用临时账户和本地合成页面的交互预览，不连接学校服务、不更改现有账户或系统设置。`--self-test` 执行界面与业务离线自检，`--backend-self-test` 执行 Edge/Chrome 后端离线自检。旧 Qt/Tk 界面保留于 `source/app.py`，需另行安装 `source/requirements-legacy.txt`。

运行后产生的 `data/` 目录包含本机账户、会话和配置，不应分享或上传。仓库及发布程序包均不包含使用者的账户数据。

## 验证与构建

```powershell
python -m unittest discover -s source/tests -v
./source/build.ps1
```

全套源码回归也覆盖保留的旧界面，需要开发环境安装 `source/requirements-legacy.txt`；轻量构建环境保持独立，不安装这些重型依赖。仅验证轻量版本可运行 `python source/lite_app.py --self-test --report work/lite-check.json`。

默认构建结果位于 `dist/release-v1.2.2/平安打卡助手/`，需要完整分发该文件夹。构建在独立环境中显式排除重型框架，并以完整目录小于 100,000,000 字节为门槛。`source/build.ps1 -LegacyQt` 可构建旧界面，需使用已安装旧版依赖的 Python。

源码已通过 230 项回归测试，完整轻量界面另有离线端到端自检。真实环境的 Edge、Chrome“仅检查”已通过登录、班次查询和浏览器读取已有手动定位配置；测试时学校尚未开放打卡，页面范围判断及真实提交未验证。学校更新页面后可能需要适配；程序不会以离线测试代替实际业务结果核验。

## 轻量版结构

完整轻量界面已接入账户、每日时间、定位、校园网、自启动、记录、停止与托盘。`CheckinService` 不依赖界面框架，`LiteBrowserRunner` 与旧浏览器共用业务判断。详见 [轻量后端说明](source/轻量后端说明.md)。`prototype/lite/` 保留前期验证版，正常使用入口为 `source/lite_app.py`。

系统缺少 WebView2 Runtime 时需要单独安装，程序不会偷偷下载浏览器。共享运行库和使用中产生的配置、日志、缓存占用另计；“小于 100 MB”统计完整解压的应用文件，而不是只看 EXE 或 ZIP。

## 项目结构

```text
source/
  app.py             桌面界面与每日调度
  glass_ui.py        Qt 界面桥接与隔离预览
  ui/               毛玻璃界面、动效和图标素材
  browser.py         登录、动态菜单入口与网页操作
  browser_flow.py    新旧浏览器共用的网页业务规则
  service.py         不依赖界面的轻量业务服务
  lite_app.py        完整轻量界面入口
  lite_bridge.py     完整界面的业务桥接与同意流程
  lite_ui/          毛玻璃页面、设置弹层与动效
  lite_browser.py    Edge/Chrome 轻量浏览器入口
  lite_cli.py        轻量后端自检与显式单次操作入口
  core.py            时间、菜单与结果解析
  storage.py         本地加密、配置与进程锁
  campus_network.py  校园网状态检查、运营商账号处理与认证
  build.ps1          Windows 构建脚本
  tests/test_app.py  自动测试
```
