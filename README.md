# ai-storage-cleaner

Jesn 的 Codex skills 合集。

## Skills

### ai-storage-cleaner

macOS / Windows 只读存储分析助手。它会扫描本机和可见外接盘，把占空间的项目分成：

- 🟢 可自动清理
- 🟡 需要确认
- 🔴 谨慎处理

核心安全原则：扫描只读；网页里的清理动作默认只把白名单路径移到废纸篓/回收站，不直接删除。

## 安装

把 `ai-storage-cleaner` 文件夹复制到 Codex skills 目录：

```bash
mkdir -p ~/.codex/skills
cp -R ai-storage-cleaner ~/.codex/skills/ai-storage-cleaner
```

重启 Codex 后即可通过 `ai-storage-cleaner` 使用。

## 运行方式

通常由 Codex 按 skill 流程自动执行：

```bash
cd ~/.codex/skills/ai-storage-cleaner
python3 scripts/scan.py > /tmp/storage_scan.json
python3 scripts/analyze.py /tmp/storage_scan.json /tmp/storage_analysis.json
python3 scripts/server.py /tmp/storage_analysis.json
```

本 skill 不依赖第三方 Python 包。
