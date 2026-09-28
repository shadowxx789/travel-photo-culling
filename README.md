# travel-photo-culling

旅行照片 / 视频选片。只移动，不删除。先扫描分析，确认后再执行。

## 初始化

```bash
cd /Users/ew/.hermes/skills/media/travel-photo-culling && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

系统依赖：`exiftool`、`ffmpeg`（macOS: `brew install exiftool ffmpeg`）。

## 运行

一律用全路径：

```bash
/Users/ew/.hermes/skills/media/travel-photo-culling/.venv/bin/python /Users/ew/.hermes/skills/media/travel-photo-culling/scripts/scan.py "<目标目录>"
```
