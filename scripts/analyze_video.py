#!/usr/bin/env python3
"""旅行视频粗筛：指标、flags、重复聚簇、拼图。只读素材，只写 99_报告/。"""
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from PIL import Image

try:
    import imagehash
except ImportError:
    sys.exit("请先安装: pip install imagehash")

from common import (CONFIG, DSU, contact_sheet, ensure_scan, gray_stats, group_median,
                    hamming, is_proxy, report_dir, require_bins, save_report)

HDR_TRANSFERS = ("arib-std-b67", "smpte2084")


# ---------- 基础工具 ----------

def grab_gray_seq(path, start, secs=1.0, size=256):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-ss", "%.2f" % start, "-i", str(path), "-t", "%.2f" % secs,
           "-vf", "scale=%d:%d,format=gray" % (size, size), "-f", "rawvideo", "-"]
    buf = subprocess.run(cmd, capture_output=True).stdout
    n = len(buf) // (size * size)
    return np.frombuffer(buf[:n * size * size], np.uint8).reshape(n, size, size)


def shake_score(path, dur, n_win=3, size=256):
    """相邻帧相位相关位移的二阶差分，数值越大说明高频抖动越明显。平滑的运镜二阶差分很小，不会被计入。"""
    import cv2
    import numpy as np
    if not dur or dur < 1.5:
        return None
    win = cv2.createHanningWindow((size, size), cv2.CV_32F)
    scores = []
    for k in range(n_win):
        start = max(0.0, dur * (k + 1) / (n_win + 1) - 0.5)
        seq = grab_gray_seq(path, start, 1.0, size)
        if len(seq) < 5:
            continue
        f = [np.float32(x) for x in seq]
        d = np.array([cv2.phaseCorrelate(f[i], f[i + 1], win)[0] for i in range(len(f) - 1)])
        acc = np.diff(d, axis=0)
        scores.append(float(np.mean(np.hypot(acc[:, 0], acc[:, 1]))))
    return round(float(np.median(scores)), 3) if scores else None


def tail_color_mode(path, nbytes=4 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            tail = f.read()
    except OSError:
        return None
    return "ilog" if b"I_Log" in tail else "standard"


# ---------- ffprobe ----------

def ffprobe_json(path):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


def parse_rate(s):
    if not s:
        return None
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            return round(float(a) / float(b), 3) if float(b) else None
        return float(s)
    except ValueError:
        return None


def probe_of(path):
    """ffprobe 原片/分析源的公共字段。duration 优先 format。"""
    d = ffprobe_json(path)
    out = {
        "width": None, "height": None, "fps": None, "codec": None, "pix_fmt": None,
        "color_transfer": None, "color_primaries": None, "has_audio": False,
        "src_w": None, "src_h": None, "src_fps": None, "probe_duration": None,
    }
    for st in d.get("streams", []):
        if st.get("codec_type") == "video" and out["width"] is None:
            out["width"] = st.get("width")
            out["height"] = st.get("height")
            out["fps"] = parse_rate(st.get("r_frame_rate"))
            out["codec"] = st.get("codec_name")
            out["pix_fmt"] = st.get("pix_fmt")
            out["color_transfer"] = st.get("color_transfer")
            out["color_primaries"] = st.get("color_primaries")
        elif st.get("codec_type") == "audio":
            out["has_audio"] = True
    out["src_w"], out["src_h"], out["src_fps"] = out["width"], out["height"], out["fps"]
    fmt = d.get("format", {})
    try:
        out["probe_duration"] = float(fmt.get("duration")) if fmt.get("duration") else None
    except ValueError:
        out["probe_duration"] = None
    return out


def mean_volume_db(path):
    cmd = ["ffmpeg", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return round(float(m.group(1)), 1) if m else None


# ---------- 主流程 ----------

def empty_rec(it):
    return {
        "id": it["id"], "primary": it["primary"], "analysis_src": None,
        "scene": it["scene"], "time": it["time"], "duration": None,
        "width": None, "height": None, "fps": None, "src_w": None, "src_h": None, "src_fps": None,
        "codec": None, "pix_fmt": None, "color_transfer": None, "color_primaries": None,
        "color_mode": None, "hdr": None, "has_audio": None, "mean_volume_db": None,
        "zoom_bucket": it.get("zoom_bucket"),
        "capture_s": it.get("capture_s"), "slowmo_factor": it.get("slowmo_factor"),
        "median_sharp": None, "brightness": [], "clip_high": [], "clip_low": [],
        "phashes": [], "shake": None, "v3_median": None, "v3_level": None,
        "v3_n": None, "v3_ratio": None, "flags": [], "dup_group": None,
        "dup_size": None, "suggested_keep": None, "sheet": None, "error": None,
        "_imgs": [],
    }


def pick_src(root, it):
    """返回 (分析源绝对路径, analysis_src 标记)。"""
    for c in it.get("companions") or []:
        if is_proxy(c):
            return root / c, "lrv"
    return root / it["primary"], "source"


def extract_frames(src, dur, w, tmpdir, vid):
    """返回 [(PIL 图, 抽帧秒数)]，临时帧用 PNG 无损，避免 JPEG 伪影干扰 Laplacian。"""
    frames = []
    for i in range(6):
        t = dur * (i + 0.5) / 6.0
        out = Path(tmpdir) / ("%s_%d.png" % (vid, i))
        cmd = ["ffmpeg", "-v", "error", "-ss", "%.2f" % t, "-i", str(src),
               "-frames:v", "1", "-vf", "scale=%d:-2" % w, "-y", str(out)]
        subprocess.run(cmd, capture_output=True)
        if not out.exists() or out.stat().st_size == 0:
            continue
        try:
            frames.append((Image.open(out).convert("RGB"), t))
        except Exception:
            continue
    return frames


def main():
    require_bins("ffmpeg", "ffprobe")
    if len(sys.argv) < 2:
        sys.exit("用法: analyze_video.py <目标目录>")
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        sys.exit("不是目录: " + str(root))

    t0 = time.time()
    data = ensure_scan(root)
    items = [it for it in data["items"] if it["kind"] == "video"]

    recs = [empty_rec(it) for it in items]
    warnings = []

    # 1) 元数据 + 分析源选择
    for it, rec in zip(items, recs):
        src, tag = pick_src(root, it)
        rec["analysis_src"] = tag
        rec["_src"] = src
        pr = probe_of(root / it["primary"])
        ps = probe_of(src) if tag == "lrv" else pr
        rec["width"], rec["height"], rec["fps"] = pr["width"], pr["height"], pr["fps"]
        rec["codec"], rec["pix_fmt"] = pr["codec"], pr["pix_fmt"]
        rec["color_transfer"], rec["color_primaries"] = pr["color_transfer"], pr["color_primaries"]
        rec["has_audio"] = pr["has_audio"]
        rec["src_w"], rec["src_h"], rec["src_fps"] = ps["src_w"], ps["src_h"], ps["src_fps"]
        dur = it.get("duration") or ps["probe_duration"] or pr["probe_duration"]
        rec["duration"] = round(dur, 2) if dur else None
        if it.get("duration") and ps["probe_duration"] and abs(it["duration"] - ps["probe_duration"]) > 1:
            warnings.append({"primary": it["primary"], "scan_duration": it["duration"],
                             "src_duration": ps["probe_duration"]})

    # 2) 统一宽度：取所有分析源宽度的最小值，且不超过 analysis_px
    widths = [r["src_w"] for r in recs if r["src_w"]]
    w = min([CONFIG["analysis_px"]] + widths) if widths else CONFIG["analysis_px"]

    # 3) 逐段分析
    with tempfile.TemporaryDirectory() as tmpdir:
        for it, rec in zip(items, recs):
            if rec["duration"] is None or rec["duration"] <= 0:
                rec["flags"] = ["ERR"]
                rec["error"] = "无法读取时长/文件可能损坏"
                continue
            src = rec.pop("_src")
            frames = extract_frames(src, rec["duration"], w, tmpdir, rec["id"])
            if not frames:
                rec["flags"] = ["ERR"]
                rec["error"] = "抽帧失败"
                continue
            rec["_imgs"] = [f for f, _t in frames]
            rec["_ts"] = [round(t, 1) for _f, t in frames]
            stats = [gray_stats(f) for f in rec["_imgs"]]
            rec["brightness"] = [s["brightness"] for s in stats]
            rec["clip_high"] = [s["clip_high"] for s in stats]
            rec["clip_low"] = [s["clip_low"] for s in stats]
            sharps = [s["sharp_p90"] for s in stats]
            sharps.sort()
            n = len(sharps)
            rec["median_sharp"] = round((sharps[n // 2] if n % 2 else (sharps[n // 2 - 1] + sharps[n // 2]) / 2), 1)
            rec["phashes"] = [str(imagehash.phash(rec["_imgs"][i])) for i in (1, 3, 4) if i < len(rec["_imgs"])]
            rec["shake"] = shake_score(src, rec["duration"])
            rec["mean_volume_db"] = mean_volume_db(root / it["primary"]) if rec["has_audio"] else None
            rec["color_mode"] = tail_color_mode(root / it["primary"])
            rec["hdr"] = rec["color_transfer"] in HDR_TRANSFERS

    # 4) V3 分组中位数（color_mode 必须是分组维度；慢动作不参与中位数计算）
    ok = [r for r in recs if r["error"] is None]
    zr = sum(1 for r in ok if r["zoom_bucket"])
    use_zoom = bool(ok) and zr / len(ok) >= 0.8
    if use_zoom:
        V3_KEYS = [
            lambda r: (r["scene"], r["color_mode"], r["zoom_bucket"]),
            lambda r: (r["scene"], r["color_mode"]),
            lambda r: (r["color_mode"], r["zoom_bucket"]),
            lambda r: (r["color_mode"],),
        ]
        v3_keys_desc = "(scene,color_mode,zoom) > (scene,color_mode) > (color_mode,zoom) > (color_mode)"
    else:
        V3_KEYS = [
            lambda r: (r["scene"], r["color_mode"]),
            lambda r: (r["color_mode"],),
        ]
        v3_keys_desc = "(scene,color_mode) > (color_mode)"
    med_in = []
    for r in ok:
        c = dict(r)
        if r["slowmo_factor"] is not None:
            c["median_sharp"] = None  # 慢动作不参与中位数计算，但仍取所在层级的中位数
        med_in.append(c)
    med = group_median(med_in, V3_KEYS, "median_sharp", CONFIG["min_group"])
    for r in ok:
        m, lvl, n = med.get(r["primary"], (None, 0, 0))
        r["v3_median"] = round(m, 1) if m is not None else None
        r["v3_level"] = lvl
        r["v3_n"] = n
        r["v3_ratio"] = round(r["median_sharp"] / m, 2) if m else None

    # 5) flags
    for r in ok:
        if r["duration"] < CONFIG["video_min_s"]:
            r["flags"].append("V1")
        skip_light = r["color_mode"] == "ilog" or r["hdr"]
        if not skip_light:
            n = len(r["brightness"]) or 1
            if sum(1 for b in r["brightness"] if b < CONFIG["dark_mean"] or b > CONFIG["bright_mean"]) / n >= 0.6:
                r["flags"].append("V5")
            if sum(1 for c in r["clip_high"] if c > CONFIG["clip_ratio"]) / n >= 0.6:
                r["flags"].append("V4-high")
            if sum(1 for c in r["clip_low"] if c > CONFIG["clip_ratio"]) / n >= 0.6:
                r["flags"].append("V4-low")
        if r["v3_median"] is not None and r["median_sharp"] < r["v3_median"] * CONFIG["blur_ratio"]:
            r["flags"].append("V3")
        # 慢放后帧间位移被压缩，shake 不可比 → 不打 V2-hint
        if r["slowmo_factor"] is None and r["shake"] is not None and r["shake"] > CONFIG["shake_hint"]:
            r["flags"].append("V2-hint")

    # 6) 重复聚簇
    dsu = DSU(len(ok))
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            a, b = ok[i], ok[j]
            da, db = a["duration"], b["duration"]
            if abs(da - db) > 0.2 * max(da, db):
                continue
            if not a["phashes"] or len(a["phashes"]) != len(b["phashes"]):
                continue
            dists = [hamming(int(x, 16), int(y, 16)) for x, y in zip(a["phashes"], b["phashes"])]
            if sum(dists) / len(dists) <= CONFIG["dup_phash"]:
                dsu.union(i, j)
    dup_n = 0
    for members in dsu.groups():
        dup_n += 1
        label = "VD%03d" % dup_n
        rows = [ok[i] for i in members]
        clean = [r for r in rows if not ({"V1", "V3", "V4-high", "V4-low", "V5"} & set(r["flags"]))]
        pool = clean or rows
        keep = max(pool, key=lambda r: (r["median_sharp"] or 0, r["duration"] or 0))
        for r in rows:
            r["dup_group"] = label
            r["dup_size"] = len(rows)
            r["suggested_keep"] = keep["primary"]

    # 7) 拼图
    sdir = report_dir(root) / "sheets" / "video"
    sdir.mkdir(parents=True, exist_ok=True)
    sheet_n = 0
    for r in recs:
        if not r["_imgs"]:
            continue
        suffix = "_LOG" if r["color_mode"] == "ilog" else ("_HDR" if r["hdr"] else "")
        if r["slowmo_factor"] is not None:
            suffix += "_SLOW"
        name = "%s_%s%s.jpg" % (r["scene"], r["id"], suffix)
        out = sdir / name
        labels = ["%s t=%.1fs" % (r["id"], t) for t in r["_ts"]]
        contact_sheet(r["_imgs"], labels, str(out), cols=3, cell=480)
        r["sheet"] = "99_报告/sheets/video/" + name
        sheet_n += 1

    for r in recs:
        r.pop("_imgs", None)
        r.pop("_ts", None)
        r.pop("_img", None)
        r.pop("_src", None)

    # 8) meta 与输出
    flag_counts = {}
    for r in recs:
        for f in r["flags"]:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    shake_stats = {}
    groups = {}
    for r in recs:
        if r["shake"] is None:
            continue
        if r["slowmo_factor"] is not None:
            key = "slowmo"
        else:
            key = "%s x %s" % (r["color_mode"], r["src_fps"])
        groups.setdefault(key, []).append(r["shake"])
    for key in sorted(groups):
        v = sorted(groups[key])
        nn = len(v)
        shake_stats[key] = {
            "n": nn, "min": v[0], "max": v[-1],
            "median": v[nn // 2] if nn % 2 else round((v[nn // 2 - 1] + v[nn // 2]) / 2, 3),
        }
    dup_groups = {}
    for r in recs:
        if r["dup_group"]:
            dup_groups[r["dup_group"]] = r["dup_size"]

    meta = {
        "video_total": len(recs),
        "analysis_width": w,
        "analysis_src": dict(Counter(r["analysis_src"] for r in recs)),
        "color_mode": dict(Counter(r["color_mode"] for r in recs)),
        "hdr": dict(Counter(r["hdr"] for r in recs)),
        "flag_counts": flag_counts,
        "v3_keys": v3_keys_desc,
        "v3_level_dist": dict(Counter(r["v3_level"] for r in ok)),
        "dup_groups": dup_groups,
        "shake_stats": shake_stats,
        "warnings": warnings,
    }
    out_path = save_report(root, "video_analysis.json", meta, recs)
    dt = time.time() - t0
    print("视频 %d：ERR %d，V1 %d，V2-hint %d，V3 %d，V4-high %d，V4-low %d，V5 %d，dup 簇 %d，拼图 %d 张，用时 %.1fs"
          % (len(recs), flag_counts.get("ERR", 0), flag_counts.get("V1", 0),
             flag_counts.get("V2-hint", 0), flag_counts.get("V3", 0),
             flag_counts.get("V4-high", 0), flag_counts.get("V4-low", 0),
             flag_counts.get("V5", 0), dup_n, sheet_n, dt))
    print("→ " + str(out_path))


if __name__ == "__main__":
    main()
