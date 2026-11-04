# 平安校园打卡 · 版本目录

程序、发布包和源码按 1.0、1.1、1.2 三个系列独立保存。日常开发使用 `1.2/source`，当前轻量版为 **1.2.4**（轻量动态背景）。

| 系列 | 主源码版本与入口 | 程序与安装包 | 版本说明 |
| --- | --- | --- | --- |
| [1.0](1.0/) | Git `v1.0.14`：[source/app.py](1.0/source/app.py)；1.0.1–1.0.13 原始源码保存在 `源码历史` | `1.0/dist`、`1.0/发布包`，原根目录已安装程序保存在 `1.0/已安装程序/1.0.14` | [1.0 说明](1.0/版本说明.md) |
| [1.1](1.1/) | Git `v1.1.1`：[source/app.py](1.1/source/app.py) | `1.1/dist` 保留 1.1.0、1.1.1 及正式发布构建，`1.1/发布包` 保存 ZIP | [1.1 说明](1.1/版本说明.md) |
| [1.2](1.2/) | 当前完整工作源码 `1.2.4`：[source/lite_app.py](1.2/source/lite_app.py) | `1.2/dist` 保留各版程序；ZIP 位于 `1.2/发布包` | [1.2 说明](1.2/版本说明.md) |

下载：[1.2.4 完整程序包](https://github.com/6sakura6/CSU-/releases/download/v1.2.4/CSU-Peace-Checkin-Windows-v1.2.4.zip) · [发布说明](https://github.com/6sakura6/CSU-/releases/tag/v1.2.4)。

本地最新程序位于 `1.2/dist/release-v1.2.4/平安打卡助手/平安打卡助手.exe`。程序、安装包及个人数据不提交到 Git；下载程序请使用上方 Release 链接。

首次从 GitHub 下载源码后，进入 1.2 目录创建独立环境：

```powershell
cd 1.2
python -m venv work/lite-venv
work/lite-venv/Scripts/python.exe -m pip install -r source/requirements.txt
work/lite-venv/Scripts/python.exe source/lite_app.py
```

构建时在同一 1.2 目录执行：

```powershell
./source/build.ps1
```

根目录 `work` 保留共享 Python 环境及整理、发布工具记录；1.2 的构建脚本可自动使用该环境，也支持在 `1.2/work/lite-venv` 自建环境或传入 `-Python`。各系列的源码可以单独安装其 `source/requirements.txt` 后运行。

账号、会话、定位和日志随各自原程序目录移动，保持原文件内容和 Windows 用户加密绑定。它们位于各程序的 `data` 中，已从 Git 排除；请勿分享这些目录。1.2 中使用过的配置通常位于 `1.2/dist/1.2.0/平安打卡助手/data`，可在新版底部“导入旧版数据”选择该目录。

1.1.0 和 1.2.0 没有独立保存的原始源码快照；其程序和发布包完整保留，源码来源在各系列的 `源码版本清单.json` 中明确标注。每个系列的主源码采用该系列目前最完整的版本。
