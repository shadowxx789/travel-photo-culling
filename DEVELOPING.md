# 改脚本 / 扩设备

换码或扩展设备前读这一份。SKILL.md 只留规则。

- 换码前把 `scripts/` 从当前 HEAD 拷出一份；Luna 回归用旧脚本跑基线。代码一改，旧输出就重建不了。
- 回归对比默认只认 kind、subtype、scene、zoom_bucket、flags、dup_group。新增诊断字段（lens、backup_copies、color_transfer、sharp_tile_max、tile_ratio、dup_*）不算破坏旧机身。用户点名比对的额外字段（如 r2_ratio、v3_ratio）一并比对。
- 已经 `--apply`、分档目录已在的真实行程目录不要再跑 scan/analyze：会改写里面的 `99_报告/`。回归用拍平到临时目录根下的副本；保留分档目录名会被 `SKIP_DIRS` 跳过，scan 会当成空目录。
- 从 `02_优秀` / `03_普通` 拷文件做回归时必须拍平到临时目录根下。
- 调查阶段：样本被拍平、没有 sidecar 目录或视频，只要 Model、主干配对、ShutterCount、时间字段符合设计，就不是停下来的「不符」。目录结构是要描述的现象，不是硬门槛。
- 非 Luna 的 device 用 Model 原文，不要拼 Make。Luna 仍是 Insta360 Luna Ultra，`device_key` 返回 `luna`。
- 非 Luna 的 zoom_bucket 用 40/105 硬分档，不要改 CONFIG 的 `zoom_tele_max`。Luna 镜头未匹配时仍走原来的 wide/tele/digital。
- 合成 RAW 测试文件：先对 JPG 写 exif 再 copy 成 RAW 扩展名。exiftool 对「JPEG 内容 + RAW 扩展名」直接 `-overwrite_original` 会失败。
- `attach_dup_diag` 在 `common.py`，距离函数作为参数传入。照片用单张 pHash 汉明距离；视频用 3 帧 pHash 距离的平均值，与聚簇口径一致。
- 闭眼检测两段式：YuNet（长边 1600）找框，映射回 `load_primary` 已转正的工作图，扩边 40% 再裁切送 FaceLandmarker。必须先 `exif_transpose`，否则 Orientation 错的 Luna 会把脸裁歪。
- landmarker `num_faces=3`，选关键点中心落在 YuNet 原框内且离裁切中心最近的一张；都不符合则 `unsure` / `note=no_match`。`analyze_faces` 出错返回 `(None, 错误类型)`，不把失败当成 0 张脸。
- `smile_squint`：closed 且 smile/squint 都高 → half；half 且都高 → open，不打 EYE-half。
- 裁切短边 < 256 时 LANCZOS 放大到 256 再送 landmarker；YuNet 框高 < 40px 记 `tiny`。landmarker 无匹配时扩边 80% 重试一次。
- 簇内 `rel_blink` / `eye_rel_closed_n` 仍写入 JSON，`EYE-rel` 只上拼图。`suggested_keep`：`subject_open_n` 多 → `eyes_closed_n` 少 → `sharp_p90` 高。没检测到脸或 unsure 不能靠「没有闭眼」胜出。
