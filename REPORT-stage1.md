# travel-photo-culling 改造 — 阶段 1 验收报告

- 日期：2026-09-28
- skill 目录：`/Users/ew/.hermes/skills/media/travel-photo-culling`
- 验收结论：**通过**（1 处补漏后）

---

## 1. 背景

对已有 skill 做分阶段改造（5 个阶段）。阶段 1 产物：`requirements.txt`、`README.md`、`scripts/common.py`、`scripts/scan.py`。

## 2. 预检结果

| 项 | 结果 |
|---|---|
| 备份 | `travel-photo-culling.bak-20260928` 已存在（09-28 16:26 创建），未重复备份 |
| git | 不是 git 仓库，无需提交 |
| Python | 3.11.15（代码按 3.9 兼容编写） |
| 已装包 | ImageHash 4.3.2 / numpy 2.4.6 / opencv-python 5.0.0.93 / pillow 12.3.0 / pillow_heif 1.7.0 / PyWavelets 1.9.0 / scipy 1.17.1 |

**发现与「现状」的出入**：阶段 1 的四个产物在 09-28 17:42–17:44 已被写入（疑似此前一轮改造在汇报前中断）。旧文件（SKILL.md 09-19、analyze.py 09-18、analyze_video.py 09-19）均未改动。经确认，按「已有产物视同完成、逐项验收再补漏」处理。

## 3. 阶段 1 文件清单

| 文件 | 状态 | 说明 |
|---|---|---|
| `requirements.txt` | 新建（补漏 1 行） | pillow / pillow-heif / imagehash / opencv-python / numpy |
| `README.md` | 新建 | 初始化 + 全路径运行方式 |
| `scripts/common.py` | 新建（200 行） | 扩展名表、CONFIG 阈值、DSU、分组中位数、清晰度/熵统计、拼图生成 |
| `scripts/scan.py` | 新建（587 行） | exiftool 批量索引、配对聚组、场景切分、输出 scan.json |

## 4. 验收项与实际结果

1. **Python 3.9 兼容** — 通过。`ast.parse(feature_version=(3,9))` 四个脚本全过；grep 无 `int.bit_count()`、无 `X | Y` 类型注解、无 `match` 语句。
2. **素材只读、只写 `99_报告/`** — 通过。scan.py 唯一写入口 `save_report → report_dir(root)`；无 mv/copy/rename。`contact_sheet(out_path)` 落点由调用方传入，留待阶段 2/3 验收。
3. **JSON 约定** — 通过。顶层 `{"meta": {...}, "items": [...]}`；`primary`/`companions` 均为相对目标目录的 posix 路径，程序逐条校验 NFC 规范化通过。
4. **配对逻辑（合成语料 13 文件实测）** — 通过。
   - IMG + DNG + XMP → 一条 item，companions=[dng, xmp]（不拆开）
   - IMG + 2 秒 VID → `live=True`，短片并入照片，不再误判「过短」
   - VID + LRV → 代理进 companions；缺 LRV 的视频单列提示；孤儿 LRV 单列
   - 截图 PNG（无 EXIF Make）正确识别
5. **场景切分** — 通过。10:00 簇 → S001，10:30 → S002（间隔 10 分钟切分正确）。
6. **时区校验** — 通过。文件名时间 vs EXIF 偏差 4 条警告（+28800s），tz 提示正确触发。
7. **幂等** — 通过。连跑两次 scan.json 字节级一致。
8. **设备识别** — 通过。Luna 文件名规则 → "Insta360 Luna Ultra"；其余走 EXIF Make/Model。

## 5. 补漏

- `requirements.txt`：`opencv-python-headless` → `opencv-python`。原因：venv 实际安装的是 opencv-python，headless 与其冲突（同一 cv2 命名空间），不值得为此重造 venv。

## 6. 备注（未改动，仅记录）

- `scripts/requirements.txt` 是旧脚本遗留（不在改造表内），保持原样。
- 无元数据文件（如截图）时间回退 mtime，属设计内行为；scan.json 的 `time_source` 字段注明来源。
- 测试语料位于 `/tmp/culling_test`（合成文件，可随时删）。

## 7. 下一步

等阶段 2 指令：`scripts/analyze.py` 整体重写（照片粗筛）。
