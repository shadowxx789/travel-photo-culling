#!/usr/bin/env python3
"""旅行照片粗筛：指标、flags、重复聚簇、拼图。只读素材，只写 99_报告/。"""
import io
import subprocess
import sys
import time
import warnings
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

Image.MAX_IMAGE_PIXELS = 400_000_000
ImageFile.LOAD_TRUNCATED_IMAGES = True
warnings.filterwarnings("ignore", message=".*MPO.*")

try:
    import imagehash
except ImportError:
    sys.exit("请先安装: pip install imagehash")

from common import (CONFIG, DSU, attach_dup_diag, contact_sheet, device_key, ensure_scan, gray_stats,
                    group_median, hamming, report_dir, require_heif, save_report,
                    sharpness)
from faces import analyze_faces, face_timing, fields_from_faces

HEIC_EXTS = {".heic", ".heif", ".hif"}
PHOTO_KINDS = ("image", "photo", "raw")


def _open_small(src, size=1600):
    img = Image.open(src)
    img.draft("RGB", (size, size))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((size, size))
    return img


def load_primary(path, primary_len=None):
    """返回 (img, used_fallback)。Luna 实况 MPF 结构损坏时按 primary_len 截断重读。"""
    try:
        return _open_small(path), False
    except Exception:
        if not primary_len:
            raise
        with open(path, "rb") as f:
            return _open_small(io.BytesIO(f.read(int(primary_len)))), True


def load_raw_preview(path):
    """读取 PreviewImage / JpgFromRaw / OtherImage 中长边最大的一张。返回 (img, long_side)。"""
    best = None
    best_long = -1
    for tag in ("-PreviewImage", "-JpgFromRaw", "-OtherImage"):
        data = subprocess.run(["exiftool", "-b", tag, str(path)],
                              capture_output=True).stdout
        if not data:
            continue
        try:
            probe = Image.open(io.BytesIO(data))
            long_side = max(probe.size)
        except Exception:
            continue
        if long_side > best_long:
            best_long = long_side
            best = data
    if not best:
        raise RuntimeError("RAW 无内嵌预览")
    return _open_small(io.BytesIO(best)), best_long


# R2 分组：从细到粗，第一个样本数 >= min_group 的层级生效
R2_KEYS = [
    lambda it: (device_key(it), it["scene"], it["subtype"], it["zoom_bucket"]),
    lambda it: (device_key(it), it["scene"], it["subtype"]),
    lambda it: (device_key(it), it["subtype"], it["zoom_bucket"]),
    lambda it: (device_key(it), it["subtype"]),
]


def flag_of(rec, is_screenshot, skip_r1r3=False):
    """screenshot 只记指标，不打 flag。HLG 不打 R1/R3。"""
    flags = []
    if is_screenshot:
        return flags
    if skip_r1r3:
        return flags
    if (rec["brightness"] < CONFIG["dark_mean"] or rec["brightness"] > CONFIG["bright_mean"]
            or rec["entropy"] < CONFIG["entropy_min"]):
        flags.append("R1")
    if rec["clip_high"] > CONFIG["clip_ratio"]:
        flags.append("R3-high")
    if rec["clip_low"] > CONFIG["clip_ratio"]:
        flags.append("R3-low")
    return flags


def phash_int(hexstr):
    return int(hexstr, 16)


def sharp_tile_max_of(rgb_img):
    """与 sharp_p90 同一张缩略图：4×4 共 16 块，每块用 sharpness()，取最大。不参与 flag。"""
    import cv2
    import numpy as np
    g = cv2.cvtColor(np.array(rgb_img), cv2.COLOR_RGB2GRAY)
    h, w = g.shape
    grid = 4
    vals = []
    for y in range(grid):
        for x in range(grid):
            tile = g[y * h // grid:(y + 1) * h // grid, x * w // grid:(x + 1) * w // grid]
            if tile.size == 0:
                continue
            vals.append(sharpness(tile))
    return round(float(max(vals)), 1) if vals else None


def cluster_dups(recs):
    """同 (device, subtype) 内聚簇。返回 {(primary): (dup_group, dup_size, suggested_keep)}。"""
    by_part = {}
    for r in recs:
        by_part.setdefault((device_key(r), r["subtype"]), []).append(r)
    out = {}
    dcount = 0
    for part in sorted(by_part):
        rows = by_part[part]
        dsu = DSU(len(rows))
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                dist = hamming(phash_int(a["phash"]), phash_int(b["phash"]))
                same_scene_burst = (a["scene"] == b["scene"]
                                    and abs(a["time"] - b["time"]) < CONFIG["burst_gap_s"])
                if dist <= CONFIG["dup_phash"] or (same_scene_burst and dist <= CONFIG["burst_phash"]):
                    dsu.union(i, j)
        for members in dsu.groups():
            members.sort(key=lambda i: rows[i]["time"])
            dcount += 1
            label = "D%03d" % dcount
            clean = [rows[i] for i in members if not ({"R1", "R3-high", "R3-low"} & set(rows[i]["flags"]))]
            pool = clean or [rows[i] for i in members]

            def _keep_rank(r):
                closed = r.get("eyes_closed_n")
                half = r.get("eyes_half_n")
                if closed is None:
                    closed = 0
                if half is None:
                    half = 0
                return (-closed, -half, r.get("sharp_p90") or 0)

            keep = max(pool, key=_keep_rank)
            for i in members:
                out[rows[i]["primary"]] = (label, len(members), keep["primary"])
    return out


def cross_similar(recs):
    """IMG 与 LIV 互记 similar_cross，不并入 dup_group。"""
    out = {r["primary"]: [] for r in recs}
    imgs = [r for r in recs if r["subtype"] == "IMG"]
    livs = [r for r in recs if r["subtype"] == "LIV"]
    for a in imgs:
        for b in livs:
            if a["scene"] != b["scene"]:
                continue
            if abs(a["time"] - b["time"]) > CONFIG["cross_gap_s"]:
                continue
            if hamming(phash_int(a["phash"]), phash_int(b["phash"])) <= CONFIG["burst_phash"]:
                out[a["primary"]].append(b["primary"])
                out[b["primary"]].append(a["primary"])
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: analyze.py <目标目录>")
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        sys.exit("不是目录: " + str(root))

    t0 = time.time()
    data = ensure_scan(root)
    photos = [it for it in data["items"] if it["kind"] in PHOTO_KINDS]
    if any(Path(it["primary"]).suffix.lower() in HEIC_EXTS for it in photos):
        require_heif()

    recs = []
    fallback_n = 0
    raw_preview_warn = 0
    for it in photos:
        p = root / it["primary"]
        ext = Path(it["primary"]).suffix.lower()
        hlg = ext in HEIC_EXTS and (
            "hlg" in str(it.get("color_transfer") or "").lower()
            or "arib-std-b67" in str(it.get("color_transfer") or "").lower()
        )
        rec = {
            "primary": it["primary"],
            "id": it["id"],
            "kind": it["kind"],
            "subtype": it["subtype"],
            "device": it.get("device"),
            "scene": it["scene"],
            "time": it["time"],
            "zoom_bucket": it["zoom_bucket"],
            "focal35": it["focal35"],
            "zoom_ratio": it["zoom_ratio"],
            "screenshot": bool(it.get("screenshot")),
            "hdr": bool(hlg),
            "raw_preview_px": None,
            "raw_preview_warning": False,
            "flags": [],
            "r2_level": None,
            "r2_median": None,
            "r2_ratio": None,
            "r2_n": None,
            "load_fallback": False,
            "dup_group": None,
            "dup_size": 1,
            "suggested_keep": None,
            "similar_cross": [],
            "sheet": None,
            "sheet_pos": None,
            "error": None,
            "sharp_tile_max": None,
            "tile_ratio": None,
            "dup_span_s": None,
            "dup_maxdist": None,
            "dup_loose": False,
            "faces_n": None,
            "subject_faces_n": None,
            "eyes_closed_n": None,
            "eyes_half_n": None,
            "faces": None,
            "faces_err": None,
        }
        try:
            if it["kind"] == "raw":
                img, long_side = load_raw_preview(p)
                rec["raw_preview_px"] = long_side
                if long_side < CONFIG["raw_preview_min_px"]:
                    rec["raw_preview_warning"] = True
                    raw_preview_warn += 1
            else:
                img, fb = load_primary(p, it.get("primary_len"))
                rec["load_fallback"] = fb
                fallback_n += 1 if fb else 0
            rec.update(gray_stats(img))
            rec["sharp_tile_max"] = sharp_tile_max_of(img)
            rec["phash"] = str(imagehash.phash(img))
            rec["flags"] = flag_of(rec, it.get("screenshot"), skip_r1r3=rec["hdr"])
            faces, ferr = analyze_faces(img)
            rec.update(fields_from_faces(faces, ferr))
            rec["_img"] = img
        except Exception as e:
            rec["error"] = "%s: %s" % (type(e).__name__, e)
        recs.append(rec)

    ok = [r for r in recs if r["error"] is None]

    # R2：sharp_p90 低于所用层级中位数 x blur_ratio
    med = group_median(ok, R2_KEYS, "sharp_p90", CONFIG["min_group"])
    for r in ok:
        m, lvl, n = med.get(r["primary"], (None, 4, 0))
        r["r2_level"] = lvl
        r["r2_median"] = m
        r["r2_ratio"] = round((r["sharp_p90"] or 0) / m, 2) if m else None
        r["r2_n"] = n
        if r["screenshot"]:
            continue
        if m is not None and (r["sharp_p90"] or 0) < m * CONFIG["blur_ratio"]:
            r["flags"].append("R2")
        if r.get("eyes_closed_n"):
            r["flags"].append("EYE")
        elif r.get("eyes_half_n"):
            r["flags"].append("EYE-half")

    # 诊断：tile_ratio，层级与样本数规则与 R2 完全一致，不参与 flag
    tmed = group_median(ok, R2_KEYS, "sharp_tile_max", CONFIG["min_group"])
    for r in ok:
        tm, _tlvl, _tn = tmed.get(r["primary"], (None, 4, 0))
        r["tile_ratio"] = round((r["sharp_tile_max"] or 0) / tm, 2) if tm else None

    # 重复聚簇 + 跨 subtype 相似
    dups = cluster_dups(ok)
    for r in ok:
        g = dups.get(r["primary"])
        if g:
            r["dup_group"], r["dup_size"], r["suggested_keep"] = g
    attach_dup_diag(recs, lambda a, b: hamming(phash_int(a["phash"]), phash_int(b["phash"])))
    xsim = cross_similar(ok)
    for r in ok:
        r["similar_cross"] = xsim.get(r["primary"], [])

    # 拼图：每场景按时间排序，每 9 张一张 3x3
    sheets = {}
    by_scene = {}
    for r in ok:
        by_scene.setdefault(r["scene"], []).append(r)
    sdir = report_dir(root) / "sheets" / "photo"
    sdir.mkdir(parents=True, exist_ok=True)
    for sc in sorted(by_scene):
        rows = sorted(by_scene[sc], key=lambda r: r["time"])
        for k in range(0, len(rows), 9):
            chunk = rows[k:k + 9]
            n = k // 9 + 1
            name = "%s_%02d.jpg" % (sc, n)
            out = sdir / name
            labels = []
            for pos, r in enumerate(chunk, 1):
                num = int(r["id"][1:]) if r["id"] and r["id"][1:].isdigit() else pos
                fl = ",".join(r["flags"]) if r["flags"] else "-"
                dg = r["dup_group"] or "-"
                labels.append("#%d %s %s" % (num, fl, dg))
                r["sheet"] = "99_报告/sheets/photo/" + name
                r["sheet_pos"] = pos
            contact_sheet([r["_img"] for r in chunk], labels, str(out), cols=3, cell=400)
            sheets["99_报告/sheets/photo/" + name] = len(chunk)

    for r in recs:
        r.pop("_img", None)

    # meta 与输出
    flag_counts = {}
    for r in recs:
        for f in r["flags"]:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    r2_dist = {}
    for r in recs:
        key = str(r["r2_level"])
        r2_dist[key] = r2_dist.get(key, 0) + 1
    dup_groups = {}
    for r in recs:
        if r["dup_group"]:
            dup_groups[r["dup_group"]] = r["dup_size"]
    x_pairs = sum(len(v) for v in (r["similar_cross"] for r in recs)) // 2
    errors = [r for r in recs if r["error"]]

    face_fail = sum(1 for r in recs if r.get("faces_err"))
    meta = {
        "photo_total": len(recs),
        "flag_counts": flag_counts,
        "r2_level_dist": r2_dist,
        "read_errors": len(errors),
        "load_fallback": fallback_n,
        "raw_preview_warnings": raw_preview_warn,
        "dup_clusters": len(dup_groups),
        "dup_groups": dup_groups,
        "similar_cross_pairs": x_pairs,
        "sheets": sheets,
        "face_errors": face_fail,
    }
    out = save_report(root, "analysis.json", meta, recs)
    dt = time.time() - t0
    _fn, _fs, face_ms = face_timing()
    print("照片 %d：读取失败 %d（兜底 %d），R1 %d，R2 %d，R3-high %d，R3-low %d，EYE %d，EYE-half %d，dup 簇 %d，cross %d 对，拼图 %d 张，用时 %.1fs，人脸 %.0fms/张"
          % (len(recs), len(errors), fallback_n,
             flag_counts.get("R1", 0), flag_counts.get("R2", 0),
             flag_counts.get("R3-high", 0), flag_counts.get("R3-low", 0),
             flag_counts.get("EYE", 0), flag_counts.get("EYE-half", 0),
             len(dup_groups), x_pairs, len(sheets), dt, face_ms))
    print("人脸检测失败 %d 张" % face_fail)
    print("→ " + str(out))


if __name__ == "__main__":
    main()
