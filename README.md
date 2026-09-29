# travel-photo-culling

旅行照片/视频选片与分档。只移动不删除，先出计划和预览，用户确认后才执行，可撤销。

## 初始化

```bash
cd <SKILL_DIR> && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

系统依赖：`exiftool`、`ffmpeg`（macOS: `brew install exiftool ffmpeg`）。

## 运行

一律用全路径（PY=<SKILL_DIR>/.venv/bin/python）：

```bash
$PY <SKILL_DIR>/scripts/scan.py "<目标目录>"            # Phase 1 扫描
$PY <SKILL_DIR>/scripts/analyze.py "<目标目录>"         # Phase 2 照片分析
$PY <SKILL_DIR>/scripts/analyze_video.py "<目标目录>"   # Phase 2 视频分析
$PY <SKILL_DIR>/scripts/execute.py "<目标目录>" --list | --apply | --undo | --cleanup [--yes]
```

所有输出写入 `<目标目录>/99_报告/`。流程见 SKILL.md。
