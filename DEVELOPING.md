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
- landmarker `num_faces=3`，选关键点中心落在 YuNet 原框内且离裁切中心最近的一张；都不符合则 `unsure` / `note=no_match`。`analyze_faces` 出错返回 `(None, 错误类型)`，不把失败当成 0 张脸。
- `smile_squint`：closed 且 smile/squint 都高 → half；half 且都高 → open，不打 EYE-half。
- 标定（南京芜湖拍平副本 + 盐城 /tmp 拍平副本，T_closed 0.3–0.7 扫描）：closed 查准率优先，最终 `eye_t_closed=0.50`、`eye_t_half=0.35`。低于 0.45 会把睁眼看地图打成 closed；高于 0.55 会丢掉唯一一张真闭眼。缺模型时 faces 全 null，分析不退出。
