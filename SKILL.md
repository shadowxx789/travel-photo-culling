---
name: travel-photo-culling
description: 整理旅行照片和视频：去重、筛出废片，按质量分为优秀/普通/待复核/待删除四档。只移动不删除，先出计划和预览，用户确认后才执行，可撤销。已适配 Insta360 Luna Ultra 与 Nikon Z5II（JPG+RAW）。
---

# 旅行照片/视频粗筛

## 1. 核心原则
1. 永不删除素材。只移动到分档目录，所有移动都可以用 --undo 撤销。
2. 先出计划再执行。用户没有明确说"执行"之前，不得运行 --apply。
3. 成组处理。原片、LRV、sidecar、._ 文件由 execute.py 一起移动。禁止手动 mv / cp / rm 素材。
4. 价值优先于完美。有纪念意义或唯一的记录，技术瑕疵不能作为删除理由。
5. 脚本 flag 只是候选。所有 flag 都要经过视觉确认才能用于分档；R2、V3、V2-hint、EYE、EYE-half 任何情况下都不能单独作为进入 00 的依据。
6. 不修改素材内容，不调整脚本阈值（除非用户要求）。除 99_报告/ 外，不在素材目录写任何文件。

## 2. 规则优先级
用户指令 > 误杀清单（第 8 节）> 唯一记录保底 > 硬淘汰 > 分档。发生冲突时，优先级高的规则生效。

## 3. 环境与命令
首次安装：
```bash
cd /Users/ew/.hermes/skills/media/travel-photo-culling && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# 另需 exiftool 和 ffmpeg：brew install exiftool ffmpeg
# 闭眼检测模型（不进 git）：bash scripts/fetch_models.sh
```
运行（PY=/Users/ew/.hermes/skills/media/travel-photo-culling/.venv/bin/python）：
```bash
$PY /Users/ew/.hermes/skills/media/travel-photo-culling/scripts/scan.py "<目录>"            # Phase 1
$PY /Users/ew/.hermes/skills/media/travel-photo-culling/scripts/analyze.py "<目录>"         # Phase 2 照片
$PY /Users/ew/.hermes/skills/media/travel-photo-culling/scripts/analyze_video.py "<目录>"   # Phase 2 视频
$PY /Users/ew/.hermes/skills/media/travel-photo-culling/scripts/execute.py "<目录>" --list | --apply | --undo | --cleanup [--yes]
```
所有输出都在 <目录>/99_报告/ 下。只要重跑过任何分析脚本，就必须重新生成 plan.json（execute.py 会拒绝比分析结果旧的 plan）。

## 4. 概念
- 条目：scan.json 的 items，每条由 primary 加 companions 组成。实况照片（LIV）是一个 JPG 条目；RAW+JPG 算一个条目，primary 是 JPG。非 Luna 设备也可跨目录配对（双卡：Model 相同且 ShutterCount 相同，读不到时改用 DateTimeOriginal+SubSec 且主干相同）。内容完全相同的备份副本挂为 companion，标记 backup_copy，不参与分析、不算重复。primary 优先级 JPG > HIF > RAW；只有 RAW 时 kind="raw"。
- LRV：视频的低清代理，永远是 companion。原片缺失的孤儿 LRV 不进 plan、不移动，只在汇报里提一句。
- 场景：S###，拍摄间隔超过 10 分钟或 GPS 距离超过 500 米就切分。视频按 capture_s（真实拍摄时长）计算，不按播放时长。
- 重复簇：照片用 D###，视频用 VD###。suggested_keep 只是脚本建议，最终保留哪条由视觉判断决定。similar_cross（普通照片和实况照片相似）只是提示，不算重复。dup_span_s / dup_maxdist / dup_loose 是诊断字段：簇规模 ≥ 6 或 dup_maxdist > burst_phash 则为 dup_loose，不改变聚簇规则。
- 拼图：照片 99_报告/sheets/photo/Sxxx_nn.jpg（3×3，标签格式为"#编号 flags 簇号"）；视频 sheets/video/<scene>_<id>[_LOG|_HDR][_SLOW].jpg（3×2，每格标出 t=秒）。

## 5. 分档目录与 reason
- 00_待删除：reason 必须是以下之一：模糊、过曝、欠曝、歪斜、闭眼、遮挡、误拍、污损、过短、抖动、重复。填"重复"时必须同时填 keep，keep 要在同一簇里，而且本身不能在 00。
- 01_待复核：reason 以「复核-」开头。常用的有：复核-唯一记录、复核-截图票据、复核-边界、复核-无法读取、复核-疑似抖动、复核-闭眼。
- 02_优秀、03_普通：reason 可以不填。
- reason 只能包含中文、字母、数字和 -，不超过 20 个字。

## 6. 流程
**Phase 1 扫描**：运行 scan.py，向用户汇报以下内容：照片数（其中实况多少）、视频数（有 LRV / 缺 LRV / 慢动作）、孤儿 LRV、时间跨度、场景数、时间警告。有多台设备时按设备分别报：张数、JPG+RAW 组数、仅 RAW、仅 JPG、跨目录配对数、备份副本数、时间范围。如果有时间警告或时区提示，先问用户，再往下走。

**Phase 2 分析**：运行 analyze.py 和 analyze_video.py。读取失败或抽帧失败的条目直接放进 01，reason 为"复核-无法读取"。

**Phase 3 视觉复核**（逐个场景）：
- 先把这个场景的全部照片拼图看一遍。
- 以下情况要看原图：命中 flag 的（含全部 R2 / R3 / EYE / EYE-half）；dup_loose 簇的全部成员；其余重复簇里要挑保留的；候选 02 的；需要判断闭眼、歪斜或污损的。其余看拼图即可。看原图时先用 PIL 缩到长边 1600 像素，不要直接载入超大图。
- 视频看拼图，判断模糊、曝光和内容。拼图的相邻两格间隔好几秒，不能用来判断抖动。抖动只看 shake 分数；需要时抽连续 1 秒的帧来确认。
- 每个条目用一句话记下判断。

**Phase 4 分档**：按第 7 节的标准生成 99_报告/plan.json，覆盖全部条目：
`{"items":[{"primary":"...","tier":"00|01|02|03","reason":"...","keep":"..."}]}`

**Phase 5 预览**：运行 --list，按第 9 节的格式向用户汇报，然后等用户确认或修改。用户要求修改时，先改 plan，再重新运行 --list。

**Phase 6 执行**：用户明确说"执行"后，再运行 --apply，并汇报结果。用户要撤销就运行 --undo。最后可以运行 --cleanup：先不带 --yes 列出清理项，用户同意后再加 --yes 执行。
- 执行后如需调整分档：先 --undo，修改 plan.json，再 --list，确认后再 --apply。不要在 Finder 中手动拖动素材。
- 本 skill 永不删除文件。00_待删除 由用户自行检查后手动清空；清空后，这些文件无法再用 --undo 恢复。

## 7. 分档标准
**进入 00 要同时满足三条**：视觉已确认、不在误杀清单里、不是唯一记录。
- 误拍：黑屏、纯色、口袋里误拍、全程拍地面或镜头盖。
- 模糊：主体整体失焦，或者糊到看不清。局部的动态模糊不算。
- 过曝 / 欠曝：主体细节已经无法辨认，而且不是有意为之（剪影、高调、夜景这类不算）。
- 闭眼 / 遮挡：人像主体闭眼，或者被手指、杂物挡住，并且同一簇里有更好的一张。EYE / EYE-half 必须视觉确认，不能单独进 00。处置：①在重复簇里且有睁眼的同簇成员：可按「重复」进 00，keep 填睁眼那张；②唯一记录或簇内全员闭眼：进 01（reason 复核-闭眼）或按价值留在 02/03，不得进 00；③合影里只有个别人闭眼、且没有替代张：留在 02/03，在 review_notes 里注明。
- 歪斜：明显是无意造成的倾斜，而且内容没有特殊价值。这一项要保守判断。
- 污损：镜头脏污或起雾，导致画面大面积受损。
- 过短（视频）：命中 V1，而且内容是误触；短但有内容的片段放进 01。
- 抖动（视频）：shake 分数高，而且抽出的连续帧确认画面没法看。只有 V2-hint 的放进 01，reason 为"复核-疑似抖动"。
- 重复：同一簇里没被选为保留的那些。每簇默认保留 1 条；表情或姿势明显不同的都保留。dup_loose 的簇是连成一串的，必须看全部成员，按"不同瞬间各保留 1 张"处理，不能整簇只留 1 张。

**唯一记录保底**：如果某个场景只剩这一条，或者它是某个人、某个地点唯一的记录，那么即使有缺陷也放进 01，reason 为"复核-唯一记录"。

**02 优秀**：在每个场景内按"内容价值 > 技术质量 > 构图"排序，最多取 3 条；场景照片超过 50 张时最多 5 条。技术上不能有明显缺陷。宁缺毋滥，一个场景可以一条都不选。

**01 待复核**：包括拿不准是否有意为之的、唯一记录、截图和票据、无法读取的、疑似抖动的，以及需要用户来判断人物的。

**03 普通**：其余的全部放这里。

## 8. 误杀清单（以下情况不扣分，也不作为删除理由）
- 连拍或抓拍中的局部动态模糊。例如人脸清晰，只有手或孩子的动作有点糊。
- 有意的追焦、慢门拖影、浅景深虚化。
- 夜景和低光下的噪点、偏暗、大面积暗部。V4-low 和 R3-low 在夜景里很常见。
- 剪影、逆光、高调或低调、有意倾斜的构图。
- 纹理少但清晰的画面，比如室内全景、招牌、天空、水面。它们容易被同一场景里细节多的照片比下去，从而命中 R2 或 V3。
- 云台拍摄中有意的旋转、移动延时、快速跟拍。
- I-Log 视频的灰、平、低对比。
- 慢动作视频：没有音轨、没有 LRV、近景配平滑背景导致 V3 偏低，这些都正常。
- 数码裁切档（w_crop / t_crop）画质偏软。只和同一档的照片比较。
- 截图和票据。统一放进 01，不删。
- 故意闭眼：大笑眯眼、表演、睡觉的孩子。属于误杀，不按闭眼进 00。

## 9. 汇报格式（Phase 5）
- 四档各自的条目数、文件数和总大小。
- 00：按 reason 统计数量，每种 reason 最多举 3 个例子，每个例子一句理由。
- 01：全部列出，每条一句理由。
- 02：按场景列出。
- 孤儿 LRV 和其他异常：各用一句话说明。
- 不要逐条刷屏；用户问到具体条目时再展开。

## 10. 设备适配

### Luna Ultra
- 文件名格式为 IMG_/LIV_/VID_/LRV_YYYYMMDD_HHMMSS_序号。时间取自文件名，是本地时间。
- 实况照片的 Make 字段为 QTI-AA，忽略即可。MPF 结构损坏的情况，脚本已经做了兜底读取。
- I-Log 视频的拼图文件名带 _LOG，不扣曝光和色彩分。V3 已经按 color_mode 分组计算。
- zoom_bucket 分为 w / w_crop / t / t_crop，对应 20mm 和 60mm 两颗原生镜头，以及它们的裁切档。
- 慢动作视频的拼图文件名带 _SLOW，场景分组用 capture_s。播放时长可能是拍摄时长的 8 倍。
- 云台素材正常的 shake 大约在 0.02 到 0.4 之间，shake_hint 暂定为 1.0。
- 校准记录（第一批，南京芜湖）：R2 命中的照片里没有一张是真正对焦失败，全都属于误杀清单里的情况；V2-hint 命中 0 次。以后如果出现真正模糊或抖动的样本，记录下来，供调整阈值参考。S011 视频（635/636/637）出现镜头起雾或水汽，拼图上显示为 V3，需从原片抽帧才能判断；低清拼图不能作为进入 00 的依据。
- 外置盘为 exFAT 时，macOS 会在移动主文件时自动带走并改名对应的 ._ 文件，execute.py 在日志中记为 auto，这是正常现象，不算冲突。不要手动处理 ._ 文件。
- 部分 Luna Ultra 照片的 Orientation 元数据错误（如 Rotate 180，但像素本身是正立的），导致拼图中画面倒置。遇到"整体倒置"时，先用 exiftool -Orientation 检查，并在不做 exif_transpose 的情况下查看原始像素；若是元数据问题，进 01，reason 为"复核-方向待旋转"，不要当作歪斜处理。

### Nikon Z5II
- JPG+NEF 整组同档；primary 是 JPG，NEF 为 companion。只有 NEF 时 kind="raw"，用内嵌预览分析。
- 本人使用单卡；跨目录配对和备份副本仅用于重复导入或合并目录的兜底场景。双卡逻辑仍保留：同一目录先按文件名主干配对；配不上再跨目录（Model 相同且 ShutterCount 相同；读不到 ShutterCount 时改用 DateTimeOriginal+SubSec 相同且主干相同）。移动时每个文件去 <TIER>/<它自己的原相对目录>/，整组同步加前缀和 _2。
- 备份副本：Model、ShutterCount（或时间+主干）、扩展名、大小、MD5 全部相同的第二份，标记 backup_copy，跟随 primary 移动，不参与分析、不算重复。必须先完成同目录/跨目录配对，再把副本并入已有条目；先按路径排序挑 canonical 会让备份 JPG 抢走 primary，再把另一个目录里同主干的 NEF 错配成跨目录。
- 时钟：非 Luna 时间优先级为 SubSecDateTimeOriginal → DateTimeOriginal+OffsetTimeOriginal → DateTimeOriginal（本地）→ mtime，再加上 CONFIG device_offsets[Model] 的秒数。同一目录多台设备时，拍一张手机时钟来校对，或在 device_offsets 填写偏移（例如 {"NIKON Z5_2": -120}）。
- 清晰度只和同一设备比较。R2 / 重复聚簇按 (device, subtype) 分区，不与 Luna 或其他机身聚簇。
- zoom_bucket 按 focal35：≤40 wide / ≤105 mid / >105 tele；记录 lens。
- 大光圈浅景深的背景虚化属于误杀清单，不能当作模糊进 00。
- 高速连拍和预拍（C15/C30、Pre-Release Capture）产生的大量近似照片按重复簇处理，每簇默认保留 1 张，表情或动作明显不同的都保留。
- NKSC_PARAM/ 跳过；.nksc 当 sidecar。.hif 若为 HLG 则 hdr=true，不打 R1/R3。.NEV 若 ffmpeg 解码不了：flags=["ERR"]，error="格式不支持"，不参与统计。
- 校准记录（盐城批）：R2 抽查 10 张，真模糊 4（其中 3 张隔玻璃拍动物）、浅景深虚化 4、清晰误判 2（大面积天空）；三类的 r2_ratio 完全重叠，不能靠调 blur_ratio 区分。sharp_tile_max / tile_ratio 是诊断字段、不参与任何 flag；tile_ratio 同样分不开真模糊和浅景深，不要靠它改阈值。隔着玻璃对焦失败属于真模糊，不在误杀清单里。

## 11. 禁止事项
- 手动 mv / cp / rm 素材。
- 未经确认就运行 --apply 或 --cleanup --yes。
- 把 LRV、孤儿 LRV 或 sidecar 写进 plan。
- 分析结果重跑后，继续沿用旧 plan。
- 已经 --apply 过的真实行程目录（分档目录已在）不要再跑 scan/analyze：这会改写里面的 99_报告/，回归用拍平到 /tmp 的副本。

## 12. 改脚本 / 扩设备
改脚本或扩展设备前先读 DEVELOPING.md。
