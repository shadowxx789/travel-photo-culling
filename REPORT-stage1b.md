# travel-photo-culling 改造 — 阶段 1 补充验收报告

- 日期：2026-09-28
- 性质：只读验收，未改素材、未改代码（无不符合项）
- 结论：**A 全部符合，B 两个用例通过，C 真实素材全部命中预期值**

---

## A. 规格核对（逐条）

| # | 规格 | 判定 | 代码位置 |
|---|---|---|---|
| 1 | common.py 顶层不 import cv2 / numpy | 符合 | 顶层仅 json/shutil/subprocess/sys/unicodedata/pathlib（common.py:2–7）；cv2/numpy 在函数内 import（`group_median`/`sharpness`/`gray_stats`）。验证：`/usr/bin/python3 -c "import common"`（Python 3.9.6）输出 `ok` |
| 2 | meta 含 file_count/local_tz/orphan_proxies/orphan_sidecars/time_warnings；ensure_scan 按 file_count 变化重扫 | 符合 | scan.py:573–579（meta 构造）；common.py:98–107（`stale = meta.get("file_count") != count_files(root)` → 重跑 scan.py） |
| 3 | assign_scenes 按 (time, seq) 排序，间隔起点是场景已覆盖的最晚结束时间（视频用 time_end） | 符合 | scan.py:138（sort key `(it["time"], it.get("seq") or 0)`）；scan.py:143（`gap = it["time"] - last_end`）；scan.py:149–150（`end = it.get("time_end") or it["time"]`，`last_end = max(last_end, end)`） |
| 4 | tz 提示：≥3 条警告且 ≥80% 偏移相同（15 分钟取整） | 符合 | scan.py:539（`len(warnings) < 3` 才 return）；scan.py:541（`int(round(off / 900.0)) * 900` 15 分钟取整）；scan.py:543（`top_n / len(warnings) >= 0.8`） |
| 5 | exiftool 索引键与查询键一致（nfc + resolve） | 符合 | scan.py:167 建索引 `out[nfc(str(Path(src).resolve()))] = row`；scan.py:172 查询 `index.get(nfc(str(p.resolve())), {})` |
| 6 | MotionPhoto==1 → live_embedded；LUNA_RE 文件 device 一律 Insta360 Luna Ultra | 符合 | scan.py:263–265（`v == 1 or v == "1" or v is True`）；scan.py:228–230（LUNA_RE 匹配直接返回 `"Insta360 Luna Ultra"`） |
| 7 | 条目字段齐全（23 个字段） | 符合 | scan.py:296–321 `empty_item()`：id、primary、companions、kind、subtype、seq、live、live_embedded、screenshot、pano、device、time、time_end、time_source、duration、width、height、gps、focal35、zoom_ratio、zoom_bucket、primary_len、scene |

## B. 合成测试（/tmp/culling_test）

1. **8 分钟视频 + 7 分钟后照片同场景**：`VID_20260101_100000_001.mp4`（10:00:00–10:08:00）+ `IMG_20260101_101500_002.jpg`（10:15:00）→ 均落 **S003**，间隔从视频 time_end 起算 7 分钟 < 10 分钟阈值 ✓
2. **≤4 秒 Luna VID 不被当作 Live 附属**：`VID_20260101_103000_003.mp4`（4 秒）+ 相邻序号 `IMG_20260101_103000_004.jpg` → VID 为独立视频条目（V0004，kind=video，companions=[]），不是任何条目的 companion，IMG 也未被误标 live ✓

## C. 真实素材验收（/Volumes/PS2000W/20260924 南京芜湖之旅）

scan.py 用时 4.4 秒。终端完整统计：

```
照片 121（实况 23 / RAW 配对 0 / 仅 RAW 0 / 截图 0 / 全景 0）
视频 42（有 LRV 41 / 缺 LRV 1）
  缺 LRV: VID_20260926_190446_687.mp4
孤儿 LRV 1（LRV_20260926_190913_688.lrv）
时间跨度 2026-09-24 19:03:53 → 2026-09-26 19:04:46
场景数 14
设备 1 台
  Insta360 Luna Ultra: 2026-09-24 19:03:53 → 2026-09-26 19:04:46 (163)
time_source: filename 163
时间警告 0
zoom_bucket: wide 58, tele 28, digital 12
focal35 min/median/max: 0.0 / 20.0 / 300.0
```

逐项核对（全部命中预期）：

| 项 | 预期 | 实测 | 判定 |
|---|---|---|---|
| 照片 / 实况 / live_embedded / 视频 | 121 / 23 / 23 / 42 | 121 / 23 / 23 / 42 | ✓ |
| 有 LRV | 41 | 41 | ✓ |
| 缺 LRV | VID_20260926_190446_687.mp4 | 一致 | ✓ |
| 孤儿 LRV | LRV_20260926_190913_688.lrv | 一致 | ✓ |
| meta.file_count | 205 | 205 | ✓ |
| 设备 | 1 台 Insta360 Luna Ultra | 163 条目全为该设备 | ✓ |
| 时间跨度 | 09-24 至 09-26 | 09-24 19:03:53 → 09-26 19:04:46 | ✓ |
| time_source / 时间警告 | 全 filename / 0 | filename 163 / 0 | ✓ |
| .lrv 为 primary | 无 | 无 | ✓ |
| zoom_bucket | 列出 | wide 58 / tele 28 / digital 12 / 无值 23 | ✓ |
| focal35 min/median/max | 列出 | 0.0 / 20.0 / 300.0 | ✓ |

### 场景列表（14 个）

| 场景 | 起 → 止 | 条目 | 照片/视频 |
|---|---|---|---|
| S001 | 09-24 19:03:53 → 19:07:59 | 3 | 1 / 2 |
| S002 | 09-24 19:36:43 → 20:22:02 | 41 | 30 / 11 |
| S003 | 09-24 21:10:04 → 21:18:07 | 11 | 8 / 3 |
| S004 | 09-25 16:18:35 → 16:26:56 | 10 | 10 / 0 |
| S005 | 09-25 17:24:01 → 17:38:10 | 12 | 12 / 0 |
| S006 | 09-25 19:23:02 → 19:48:23 | 14 | 12 / 2 |
| S007 | 09-25 20:06:01 → 20:06:42 | 2 | 0 / 2 |
| S008 | 09-25 20:20:58 → 20:21:58 | 5 | 4 / 1 |
| S009 | 09-25 20:38:32 → 20:49:22 | 10 | 0 / 10 |
| S010 | 09-25 21:00:17 → 21:03:02 | 2 | 1 / 1 |
| S011 | 09-25 21:14:26 → 21:15:53 | 5 | 2 / 3 |
| S012 | 09-25 21:31:52 → 21:38:12 | 6 | 0 / 6 |
| S013 | 09-26 10:59:36 → 11:22:59 | 41 | 41 / 0 |
| S014 | 09-26 19:04:46 → 19:13:03 | 1 | 0 / 1 |

合计 163 条目 = 121 照片 + 42 视频，各场景相加一致。

### 观察（非规格问题，记录备查）

- focal35 = 0.0 的 23 张与 live_embedded 的 23 张是**同一批**：实况 JPG 缺焦距元数据 → zoom_bucket 为 None（无值 23）。属素材元数据事实，不是扫描缺陷。
- S014 只有 1 个条目（VID_20260926_190446_687.mp4，即缺 LRV 的那段），time_end 19:13:03 说明该段约 8 分钟。

## 结论

阶段 1 无需修改代码。等待阶段 2 指令（analyze.py 整体重写）。
