---
name: ai-storage-cleaner
description: >
  macOS / Windows 只读存储分析助手（自动识别系统）。扫描整机磁盘占用，找出
  占空间大户，把每一项分成 🟢可自动清理 / 🟡需人工判断 / 🔴谨慎清理 三级并给出
  可执行处置方案，生成简洁好看的交互式 HTML 报告，并可起本地服务在网页上一键
  安全清理（默认只移废纸篓/回收站，不直接删）。扫描全程只读。务必在以下场景
  使用：用户说"存储分析""磁盘满了""C盘/硬盘满了""空间不够""清理空间"
  "清理磁盘""占空间""哪些东西占地方""帮我看看存储""看一下电脑存储/空间"
  "存储空间""电脑空间不够""内存满了/不够/不足""看下内存/存储"（中文口语里
  "内存"常指存储空间）"storage analysis""disk cleanup""清缓存""磁盘清理"；
  或用户抱怨电脑没空间、想知道什么东西吃硬盘、想要清理建议时。注意：若用户明确
  指运行内存/RAM（如"哪个进程吃内存""内存占用高"想看活动监视器），那是 RAM
  不是存储，不属于本 skill。
---

# AI Storage Cleaner

对 macOS / Windows 做一次只读存储分析，产出以电脑名称命名的交互式 HTML 报告。流程：扫描本机与外接盘 → 自动分析分级 → 生成网页 → 安全清理。

## 铁律

- **扫描全程只读。** 扫描/分析阶段只能跑统计、列目录、读元信息（df、du、diskutil、stat、ls）。绝对禁止 rm、mv、rmdir、清空回收站、改权限等任何写操作。
- **安全清理模式默认开启。** 网页按钮只能把白名单路径移到废纸篓/回收站，不能直接删除；清空废纸篓/回收站必须由用户自己完成。即使用户说"帮我删"，也要先让用户在报告里确认，不要在对话里直接代跑破坏性命令。
- **估算标注清楚。** 涉及"可释放空间"一律说明是估算值。
- **路径、命令保留原文不翻译。**

## 执行流程

### Step 1 扫描（只读）

```bash
python3 scripts/scan.py > /tmp/storage_scan.json
```

`scan.py` 自动识别系统（`sys.platform`）：
- **macOS**：扫 home、library、caches、containers、group_containers、app_support、applications、downloads、dev_caches，并枚举 `/Volumes` 下可见外接盘；每个外接盘会生成 `external_volumes` 摘要和 `volume_<盘名>` 子目录体量列表。子目录体量用单次 `du -k -d 1` 批量计算（比逐目录 `du -sk` 快很多）。
- **Windows**：扫 user_profile、appdata_local、appdata_roaming、temp、downloads、program_files(_x86)、dev_caches，并枚举所有盘符；非系统盘会生成 `external_volumes` 摘要和 `volume_<盘符>` 子目录体量列表，用 `os.scandir` 算大小。
- **大文件 + 重复文件（两个系统都有）**：在 home 和各外接盘根上枚举 ≥50MB 的单个大文件（macOS 用 `find -x`，Windows 用 `os.walk`），输出 `big_files`（最大单文件 Top 榜，藏在普通目录里的视频/镜像/虚拟机盘等）。再对大文件按「相同大小 → 头尾分块 sha1」找重复，输出 `duplicates`（重复副本组 + 可省空间）。这两类只读、只定位，不参与自动删除。

输出 JSON：`system`（系统/磁盘信息，含 `computer_name`、`disk_name` 主盘名 + `disks` 全部盘/外接盘）+ `groups`（各组子目录大小，已降序、过滤 50MB 以下）+ `big_files`（最大单文件）+ `duplicates`（重复文件组）。扫描较慢（大文件枚举会遍历全盘），耐心等。读不到的目录标 `denied`，需在报告里列出并提示遗漏体量。

### Step 2 分析与分级

默认先用内置启发式分析器生成第一版 analysis JSON，再由 agent 结合上下文复核、补充和改写：

```bash
python3 scripts/analyze.py /tmp/storage_scan.json /tmp/storage_analysis.json
```

`analyze.py` 会自动：
- 用 `system.computer_name` 生成 `report.title`，格式为「某个电脑的存储分析报告」。
- 汇总本机系统盘和外接盘的大目录，识别缓存、安装包、开发缓存、外接盘大目录和大应用。
- 给可移废纸篓/回收站的项写入 `trash_paths`，供网页按钮白名单使用。
- 透传 `big_files`（最大单文件，按扩展名标内容类型：视频/镜像/虚拟机镜像/压缩包等）和 `duplicates`（重复文件组 + 每组可省空间）。
- 生成 `top5`、`summary.tier_stats` 和清理卡片。

之后看 `system.os` 判断系统，读对应的数据布局参考：macOS 读 [references/macos.md](references/macos.md)，Windows 读 [references/windows.md](references/windows.md)（讲该系统东西存哪、怎么辨认、归哪一级）。然后读 `/tmp/storage_scan.json` + `/tmp/storage_analysis.json` 做复核：

1. **挑 Top 5** 占用大户，判定类型（系统资产/应用本体/应用数据/应用缓存/开发缓存/用户文件/媒体内容/下载内容/虚拟机镜像/回收站/其他）。
2. **识别"神秘大目录"**：UUID 命名的 Container、不明的隐藏目录，要追查它属于哪个 App、装的是什么（例如某 97GB 的 UUID Container 实为 Bilibili 离线视频缓存）。必要时 `ls`/`du` 深入一层看清楚，但仍只读。
3. **三级分类 = 清理决策清单，不是全盘点。** 只把"存在'要不要动它'这个决策"的项放进三灯；日常在用的正常应用、操作系统本身、海量零碎小文件没有清理决策，不进三灯，它们落在磁盘条的蓝色"系统及其他"里。判定标准：
   - 🟢 **可自动清理**：纯缓存、临时文件、安装包残留、明确可再生且不影响功能、不丢用户数据（pip/uv/npm/Xcode DerivedData 等开发缓存、浏览器缓存）。
   - 🟡 **需人工判断**：含用户数据或有判断成本（离线视频、文档、项目代码 node_modules、聊天记录、设计稿）。给内容画像、处置路径和风险提示。**所有橙灯项在服务模式下自动有「在访达/资源管理器打开」按钮**；如果它是核实过的安全子路径，或外接盘上一个明确可由用户整体确认的候选目录，给它 `trash_paths` → 网页出现「移到废纸篓」按钮。App 托管又无安全子路径的（Chrome/微信）只给打开按钮、不给 `trash_paths`。如果某项在文件管理器里是 App 内部格式、不方便手动挑选，给它一个 `open_note` 字段做客观说明。
   - 🔴 **谨慎清理（有决策但不建议手删）**：你可能想动、但建议别手删的具体项——重复安装的应用、想卸载的大应用、长期不用的大项目、运行中应用的核心数据等。给"为什么不建议手删" + `indirect_release` 写具体卸载步骤。应用项给 `app_paths`（真实 `.app` 绝对路径数组）→ 网页出现「在文件管理器打开（去卸载）」按钮，定位到 App 让用户自己正规卸载。**红灯不给删除/卸载按钮**。纯系统文件、APFS 快照不要单独列红卡，归蓝色即可。

每个 🟢 项要给：预估释放空间、清理前需关闭的进程和 `trash_paths`。

**大小字段写干净**：`size` / `size_estimate` 用"约 14 GB""合计约 8.6 GB"即可——"约"已表示估算，不要再加"（估算）"，重复且不专业（模板也会自动去掉这种冗余括号）。可再生属性已由分级标题和按钮说明覆盖，别塞进大小字段。

### Step 3 生成交互报告

把复核后的分析结果写成 analysis JSON（schema 见 `scripts/build_report.py` 顶部注释）。报告标题必须来自 `report.title`，优先使用 `system.computer_name`，形如「Jesn 的 MacBook Pro 的存储分析报告」。

**🟢 项必须带 `trash_paths`**（具体可删的绝对路径数组，区别于人类可读的 `path` 展示字段）——这是网页删除按钮的前提，漏了按钮就不出现。

**默认用安全清理模式（`server.py`）打开报告**，因为这个 skill 的核心价值就是网页上能直接把报告白名单路径移到废纸篓/回收站：
```bash
python3 scripts/server.py /tmp/storage_analysis.json   # 自动开浏览器，Ctrl+C 停
```
`server.py` 起在 127.0.0.1 + 随机端口 + 随机 token。🟢 项给「移到废纸篓/回收站」；🟡 项给「在访达打开」+（有安全子路径时）「移到废纸篓/回收站」；🔴 项只给打开位置或正规卸载建议；**大文件榜和重复文件组只给「在访达打开」**（只读定位，不参与自动删除）。**安全模型**：`trash` 只允许 green/yellow 的 `trash_paths`，这些白名单路径可以来自主目录，也可以来自外接盘；`open` 允许上述全部 + 橙灯真实 `path` + 红灯 `app_paths` + `big_files` 路径 + `duplicates` 所有路径。所有请求 realpath 校验 + 精确白名单校验 + token + Host 校验，每次点击浏览器先 confirm。osascript/SHFileOperationW 入废纸篓，macOS 首次弹访达自动化授权点允许即可。

仅当用户明确只想要一份可分享/留存的只读文件时，才用静态模式（无删除按钮，因为 `file://` 打开的页面碰不到文件系统）：
```bash
python3 scripts/build_report.py /tmp/storage_analysis.json ~/Desktop/storage-report.html && open ~/Desktop/storage-report.html
```

**排障：网页上没有移废纸篓按钮** = 要么开的是静态报告（改用 `server.py`），要么 🟢 项漏了 `trash_paths`（补上重启服务）。

报告阅读流（固定顺序）：磁盘总览卡片（容量 + 分段进度条 + 三色图例 + 系统信息）→ 占用排行 Top5 → 🟢🟡🔴 三组视觉卡片 → 最大单文件 Top 榜 → 重复文件组 → 总结与建议。即「现状 → 最大占用 → 可操作项 → 大文件/重复 → 建议」。UI 是浅色极简线条风（白底 + 发丝级 1px 细线 + 大留白 + 黑灰主色 + 单一靛蓝强调色 + 等宽数字），符合阅读习惯、信息密度高、文案短直接。

注意 `summary.overview` 要写成一句话洞察（直接说最大占用是什么、能释放多少），不要重复总/已用/可用数字——那些已在卡片大数字里显示。

磁盘进度条把"已用"拆成分段：绿(可自动清)+橙(需手动)+红(已识别的不建议动项)+蓝(系统及其他，自动取 已用−绿−橙−红 的余量)，余下为可用(灰底)。`summary.tier_stats` 的 green / yellow / red 三个值都要以可解析的 GB 数字开头（如 "约 27.8 GB"），脚本从中取数算分段；蓝色段和"系统及其他"pill 由模板自动算余量。

pills 只渲染解析出的纯数字（如"约 5.5 GB"），不显示数据里的附注，所以 tier_stats 三个值写干净的数字即可，别加"仅已识别项/系统未计"这类道歉式说明——系统文件本来就归在蓝色段，红色只放你能量化的 🔴 项（重复应用、可卸载大应用等），量不准的系统文件/快照自然落到蓝色。

### Step 4 对话里给摘要

报告生成后，在对话里用一段话给结论先行的摘要：总可释放估算、最该先清的 2-3 项、风险最高的一项。细节让用户看网页。

## 依赖与运行前提

- 全部脚本是 **Python 3 标准库**，零第三方依赖（不用 pip install）。
- **macOS** 自带 python3、`du`、`diskutil`、`osascript`，开箱即用。
- **Windows** 默认没装 Python——需先装 Python 3，且命令多为 `python` 或 `py -3`（不是 `python3`）。本 skill 命令示例写的是 `python3`，在 Windows 上自动改用 `python` / `py -3`。
- 本 skill 是 **agent 驱动**：扫描出数据后由 agent（Claude）做分级分析，不是双击即用的独立 App。

## 平台状态

- **macOS**：完整实现并实测（扫描 / 报告 / 一键安全清理全验证过）。
- **Windows**：代码已写（`scan.py` 的 `scan_windows`、`server.py` 的 `_trash_windows` 走 `SHFileOperationW`），但**未在真实 Windows 上实测**。首次在 Windows 跑要核对：目标目录路径、`os.scandir` 大小、回收站删除是否正常。多盘符已支持（主盘分段条 + 其他盘列表）。

## 长期优化建议素材（写进报告 summary.long_term）

- 定期清理：`brew cleanup`、Xcode DerivedData、浏览器缓存
- 可视化工具：DaisyDisk、GrandPerspective、OmniDiskSweeper
- 大文件归档到外置盘 / iCloud / NAS；macOS「系统设置 > 通用 > 储存空间」的优化选项
