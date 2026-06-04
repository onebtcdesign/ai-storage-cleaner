# ai-storage-cleaner

一个给 Codex / AI Agent 使用的只读存储分析 skill，用来快速看清 macOS / Windows 电脑和外接盘里到底是什么占空间。

## ai-storage-cleaner

`ai-storage-cleaner` 是一个 macOS / Windows 只读存储分析 skill。它会扫描本机和可见外接盘，找出最占空间的目录、单个大文件和重复文件，并把可处理项分成三类：

- 🟢 可自动清理：缓存、临时文件、开发缓存等可再生内容。
- 🟡 需要确认：视频素材、项目归档、聊天附件、浏览器 Profile 等用户数据。
- 🔴 谨慎处理：应用本体或不建议手动删除的核心数据，只给定位和卸载建议。

核心安全原则：扫描阶段只读；网页里的清理动作默认只把白名单路径移到废纸篓/回收站，不直接删除。清空废纸篓/回收站需要用户自己完成。

## 一键安装

### 方式一：让 AI 来安装（推荐）

把这个仓库地址发给 Codex 或你正在使用的 AI 编程助手：

```text
https://github.com/onebtcdesign/ai-storage-cleaner
```

然后对它说：

```text
请帮我安装这个 Codex skill 到 ~/.codex/skills/ai-storage-cleaner，并确认安装后可以被调用。
```

AI 会帮你 clone 仓库、复制 `ai-storage-cleaner/` 目录到本机 skill 目录，并检查安装结果。安装完成后重启 Codex。

### 方式二：手动安装

在这个仓库根目录运行：

```bash
mkdir -p ~/.codex/skills && rm -rf ~/.codex/skills/ai-storage-cleaner && cp -R ai-storage-cleaner ~/.codex/skills/ai-storage-cleaner
```

重启 Codex 后，直接说：

```text
请调用 ai-storage-cleaner 帮我分析电脑存储
```

## 怎么使用

通常不需要手动运行脚本，直接在 Codex 里发起请求即可：

```text
帮我看看电脑存储
磁盘满了，帮我分析一下哪些东西占空间
清理空间，先生成一份安全报告
```

Codex 会按 skill 流程自动执行：

```bash
cd ~/.codex/skills/ai-storage-cleaner
python3 scripts/scan.py > /tmp/storage_scan.json
python3 scripts/analyze.py /tmp/storage_scan.json /tmp/storage_analysis.json
python3 scripts/server.py /tmp/storage_analysis.json
```

`server.py` 会启动一个只绑定 `127.0.0.1` 的本地网页报告服务。报告里的按钮支持：

- 🟢 绿灯项：在访达/资源管理器打开、移到废纸篓/回收站、批量移到废纸篓/回收站。
- 🟡 黄灯项：先打开路径确认；只有明确安全的候选目录才提供移到废纸篓/回收站。
- 🔴 红灯项：只定位到应用或目录，提示正规卸载/应用内清理，不提供删除按钮。
- 大文件和重复文件：只提供打开路径，方便用户人工判断，不参与自动删除。

## 效果有哪些

- 生成一份交互式 HTML 存储报告。
- 展示系统盘容量、已用/可用空间、外接盘占用和分段图例。
- 给出占用排行 Top 5。
- 自动识别可再生缓存、聊天/浏览器/项目数据、大应用候选。
- 枚举最大的单个文件，比如视频、镜像、压缩包、虚拟机文件。
- 识别大文件重复副本，估算保留 1 份后可节省的空间。
- 所有自动清理都走白名单和浏览器确认，只移到废纸篓/回收站。

## 手动生成静态报告

如果只想留存一份不能清理的静态 HTML 文件：

```bash
python3 scripts/build_report.py /tmp/storage_analysis.json ~/Desktop/storage-report.html
open ~/Desktop/storage-report.html
```

静态报告没有清理按钮，因为 `file://` 页面不能安全访问本机文件系统。

## 依赖

脚本只使用 Python 3 标准库，不需要 `pip install`。

- macOS：依赖系统自带的 `du`、`diskutil`、`find`、`osascript`。
- Windows：需要先安装 Python 3，清理动作使用系统回收站 API。
