# travel-photo-culling

旅行照片/视频选片与分档工具。去重、筛废片，按质量分四档。**只移动不删除**，先出计划和预览，确认后才执行，随时可撤销。已适配 Insta360 Luna Ultra。

## 特性

- **六阶段流程**：扫描 → 分析 → 视觉复核 → 分档 → 预览确认 → 执行/撤销
- **四档目录**：`00_待删除` / `01_待复核` / `02_优秀` / `03_普通`，淘汰原因统一前缀命名（如 `重复__IMG_xxx.jpg`）
- **成组处理**：原片、LRV 代理、sidecar、AppleDouble `._` 文件一起移动，保持同名关系
- **安全机制**：
  - plan.json 覆盖全部条目才执行，漏项/非法理由/分析结果过期一律整体拒绝
  - 撞名整组自动加 `_2`（`_3`…）；计划外的目标占用在 `os.rename` 前直接中止，绝不覆盖
  - 每个文件的移动先写日志再 rename，中断可恢复；`--undo` 倒序还原全部移动
- **Luna Ultra 适配**：LRV 代理抽帧、实况照片 MPF 损坏兜底读取、I-Log 尾部标记识别、慢动作 capture_s/播放时长区分、exFAT 自动带走 `._`、Orientation 元数据错误识别

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 系统依赖
brew install exiftool ffmpeg
```

需要 Python 3.9+。

## 使用

```bash
PY=.venv/bin/python

$PY scripts/scan.py "<目录>"            # 扫描：元数据、配对、场景切分
$PY scripts/analyze.py "<目录>"         # 照片分析：指标、去重、拼图
$PY scripts/analyze_video.py "<目录>"   # 视频分析：抽帧、抖动、去重、拼图

$PY scripts/execute.py "<目录>" --list              # 预览（只读）
$PY scripts/execute.py "<目录>" --apply             # 执行移动（需用户确认后）
$PY scripts/execute.py "<目录>" --undo              # 撤销整个批次
$PY scripts/execute.py "<目录>" --cleanup [--yes]   # 清理孤儿 ._ 与空目录
```

所有输出写入 `<目录>/99_报告/`（`scan.json` / `analysis.json` / `video_analysis.json` / `plan.json` / `sheets/` 拼图 / 移动日志）。只要重跑过分析脚本，就必须重新生成 plan.json。

`plan.json` 由使用者按分档标准生成：

```json
{"items": [{"primary": "相对路径", "tier": "00|01|02|03", "reason": "重复", "keep": "同簇保留项"}]}
```

完整的流程、分档标准、误杀清单与汇报格式见 [SKILL.md](SKILL.md)。

## 注意

- 素材目录除 `99_报告/` 外不写任何文件；脚本对素材只读（execute.py 除外）
- `00_待删除` 只是移动分档，最终由使用者检查后手动清空；清空后无法再 `--undo` 恢复
- 测试钩子：`CULL_CRASH_AFTER=N`、`CULL_CRASH_MODE=before_done` 模拟移动中断（仅用于回归测试）
