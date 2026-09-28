#!/usr/bin/env python3
import math
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import *

LUNA_RE = re.compile(r"^(IMG|LIV|VID|LRV)_(\d{8})_(\d{6})_(\d+)$", re.I)
DT_FMTS = ("%Y:%m:%d %H:%M:%S.%f%z", "%Y:%m:%d %H:%M:%S%z",
           "%Y:%m:%d %H:%M:%S.%f", "%Y:%m:%d %H:%M:%S")
EXIF_TAGS = [
    "-SubSecDateTimeOriginal", "-DateTimeOriginal", "-OffsetTimeOriginal",
    "-CreationDate", "-CreateDate", "-Make", "-Model",
    "-ImageWidth", "-ImageHeight",
    "-FocalLengthIn35mmFormat#", "-DigitalZoomRatio#", "-Duration#",
    "-GPSLatitude#", "-GPSLongitude#",
    "-UserComment", "-ContentIdentifier", "-ProjectionType",
    "-DirectoryItemLength", "-MotionPhoto",
]
SKIP_I = ["-i", "00_待删除", "-i", "01_待复核", "-i", "02_优秀", "-i", "03_普通", "-i", "99_报告"]
HEIC_EXTS = {".heic", ".heif"}
JPG_EXTS = {".jpg", ".jpeg"}


def get_tz():
    s = CONFIG["local_tz"]
    if not s:
        return datetime.now().astimezone().tzinfo
    sign = 1 if s[0] == "+" else -1
    h, m = map(int, s[1:].split(":"))
    return timezone(sign * timedelta(hours=h, minutes=m))


def tz_offset_str(tz):
    off = datetime.now(tz).utcoffset() or timedelta(0)
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return "%s%02d:%02d" % (sign, total // 3600, (total % 3600) // 60)


def classify(p):
    ext = p.suffix.lower()
    if ext in PROXY_EXTS or p.name.upper().startswith("LRV_"):
        return "proxy"
    if ext in SIDECAR_EXTS:
        return "sidecar"
    if ext in RAW_EXTS:
        return "raw"
    if ext in VIDEO_EXTS:
        return "video"
    return "image"


def group_key(root, p):
    parent = rel(root, p.parent) if p.parent.resolve() != Path(root).resolve() else ""
    m = LUNA_RE.match(p.stem)
    if m:
        return (parent, m.group(2), m.group(3), m.group(4))
    return (parent, p.stem.lower())


def parse_dt(s):
    if not isinstance(s, str) or s.startswith("0000"):
        return None
    for fmt in DT_FMTS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def filename_time(p, tz):
    m = LUNA_RE.match(p.stem)
    if not m:
        return None
    return datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S").replace(tzinfo=tz).timestamp()


def exif_time(ex, is_video, tz):
    for tag in ("SubSecDateTimeOriginal", "DateTimeOriginal"):
        dt = parse_dt(ex.get(tag))
        if dt:
            return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).timestamp(), tag
    dt = parse_dt(ex.get("CreationDate"))
    if dt and dt.tzinfo:
        return dt.timestamp(), "CreationDate"
    dt = parse_dt(ex.get("CreateDate"))
    if dt:
        return dt.replace(tzinfo=timezone.utc if is_video else tz).timestamp(), "CreateDate"
    return None, None


def item_time(p, ex, is_video, tz, warnings):
    """warnings 追加 (文件名, 偏移秒数)。"""
    ft = filename_time(p, tz)
    et, etag = exif_time(ex, is_video, tz)
    if ft is not None:
        if et is not None and abs(ft - et) > 60:
            warnings.append((p.name, int(et - ft)))
        return ft, "filename"
    if et is not None:
        return et, etag
    return p.stat().st_mtime, "mtime"


def first_len(v):
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip():
        return int(v.replace(",", " ").split()[0])
    return None


def zoom_bucket(f35, ratio, device):
    if not f35:
        return None
    r = ratio or 1.0
    if device == "Insta360 Luna Ultra":
        base = f35 / r
        for name, mm in CONFIG["luna_lenses"].items():
            if abs(base - mm) <= mm * 0.1:
                return name + ("_crop" if r > 1.05 else "")
    if f35 <= CONFIG["zoom_wide_max"]:
        return "wide"
    return "tele" if f35 <= CONFIG["zoom_tele_max"] else "digital"


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lon2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))


def assign_scenes(items):
    items.sort(key=lambda it: (it["time"], it.get("seq") or 0))
    sid, last_end, last_gps = 1, None, None
    for it in items:
        new = False
        if last_end is not None:
            gap = it["time"] - last_end
            far = bool(it.get("gps") and last_gps and haversine(it["gps"], last_gps) > CONFIG["scene_gps_m"])
            if gap > CONFIG["scene_gap_min"] * 60 or far:
                sid += 1
                new = True
        it["scene"] = "S%03d" % sid
        end = it.get("time_end") or it["time"]
        last_end = end if (last_end is None or new) else max(last_end, end)
        last_gps = it.get("gps") or last_gps


def run_exiftool(root):
    cmd = [
        "exiftool", "-json", "-r", "-q", "-q", "-api", "LargeFileSupport=1",
    ] + EXIF_TAGS + SKIP_I + [str(root)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not r.stdout.strip():
        return {}
    rows = json.loads(r.stdout)
    out = {}
    for row in rows:
        src = row.get("SourceFile")
        if not src:
            continue
        out[nfc(str(Path(src).resolve()))] = row
    return out


def ex_of(index, p):
    return index.get(nfc(str(p.resolve())), {}) or {}


def as_float(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", " ").split()[0])
        except ValueError:
            return None
    return None


def duration_of(ex):
    return as_float(ex.get("Duration"))


def wh_of(ex):
    w = ex.get("ImageWidth")
    h = ex.get("ImageHeight")
    try:
        w = int(w) if w is not None else None
    except (TypeError, ValueError):
        w = None
    try:
        h = int(h) if h is not None else None
    except (TypeError, ValueError):
        h = None
    return w, h


def gps_of(ex):
    lat = as_float(ex.get("GPSLatitude"))
    lon = as_float(ex.get("GPSLongitude"))
    if lat is None or lon is None:
        return None
    return [lat, lon]


def image_rank(p):
    ext = p.suffix.lower()
    if ext in HEIC_EXTS:
        return 0
    if ext in JPG_EXTS:
        return 1
    return 2


def luna_meta(p):
    m = LUNA_RE.match(p.stem)
    if m:
        return m.group(1).upper(), int(m.group(4))
    return None, 0


def device_of(p, ex):
    if LUNA_RE.match(p.stem):
        return "Insta360 Luna Ultra"
    make = (ex.get("Make") or "").strip()
    model = (ex.get("Model") or "").strip()
    name = (make + " " + model).strip()
    return name or None


def is_screenshot(p, ex):
    name = p.name.lower()
    keys = ("screenshot", "screen shot", "截屏", "屏幕快照", "屏幕截图")
    if any(k in name or k in p.name for k in keys):
        return True
    if ex.get("UserComment") == "Screenshot":
        return True
    if p.suffix.lower() == ".png" and not ex.get("Make"):
        return True
    return False


def is_pano(ex, w, h):
    proj = (ex.get("ProjectionType") or "")
    if isinstance(proj, str) and proj.lower() == "equirectangular":
        return True
    if w and h:
        long_side = max(w, h)
        short_side = min(w, h)
        if short_side and long_side >= 6000:
            ratio = long_side / float(short_side)
            if 1.95 <= ratio <= 2.05:
                return True
    return False


def is_live_embedded(ex):
    v = ex.get("MotionPhoto")
    return v == 1 or v == "1" or v is True


def is_proxy_path(path_s):
    name = Path(path_s).name
    return Path(path_s).suffix.lower() in PROXY_EXTS or name.upper().startswith("LRV_")


def is_raw_path(path_s):
    return Path(path_s).suffix.lower() in RAW_EXTS


def merge_groups(groups, index, root):
    keys = list(groups.keys())
    dsu = DSU(len(keys))
    cid_first = {}
    for i, k in enumerate(keys):
        for p in groups[k]:
            cid = ex_of(index, p).get("ContentIdentifier")
            if not cid:
                continue
            if cid in cid_first:
                dsu.union(cid_first[cid], i)
            else:
                cid_first[cid] = i
    merged = defaultdict(list)
    for i, k in enumerate(keys):
        merged[dsu.find(i)].extend(groups[k])
    return list(merged.values())


def empty_item():
    return {
        "id": None,
        "primary": None,
        "companions": [],
        "kind": None,
        "subtype": None,
        "seq": 0,
        "live": False,
        "live_embedded": False,
        "screenshot": False,
        "pano": False,
        "device": None,
        "time": None,
        "time_end": None,
        "time_source": None,
        "duration": None,
        "width": None,
        "height": None,
        "gps": None,
        "focal35": None,
        "zoom_ratio": None,
        "zoom_bucket": None,
        "primary_len": None,
        "scene": None,
    }


def fill_media_fields(it, p, ex):
    w, h = wh_of(ex)
    it["width"] = w
    it["height"] = h
    it["gps"] = gps_of(ex)
    f35 = as_float(ex.get("FocalLengthIn35mmFormat"))
    it["focal35"] = f35 if f35 else None
    zr = as_float(ex.get("DigitalZoomRatio"))
    it["zoom_ratio"] = zr if zr else None
    dev = device_of(p, ex)
    it["device"] = dev
    bucket = zoom_bucket(f35, zr, dev)
    if dev == "Insta360 Luna Ultra" and f35 and bucket in ("wide", "tele", "digital"):
        print("警告: Luna 焦距/倍率未匹配镜头: %s focal35=%s zoom_ratio=%s" % (p.name, f35, zr))
    it["zoom_bucket"] = bucket
    it["primary_len"] = first_len(ex.get("DirectoryItemLength"))
    it["screenshot"] = is_screenshot(p, ex)
    it["pano"] = is_pano(ex, w, h)


def make_photo_item(root, primary, companions, ex, tz, warnings, live, live_embedded):
    it = empty_item()
    prefix, seq = luna_meta(primary)
    it["primary"] = rel(root, primary)
    it["companions"] = [rel(root, c) for c in companions]
    it["kind"] = "image"
    it["subtype"] = prefix if prefix else "IMG"
    it["seq"] = seq
    it["live"] = bool(live or live_embedded)
    it["live_embedded"] = bool(live_embedded)
    t, src = item_time(primary, ex, False, tz, warnings)
    it["time"] = t
    it["time_end"] = None
    it["time_source"] = src
    it["duration"] = None
    fill_media_fields(it, primary, ex)
    return it


def make_raw_item(root, primary, companions, ex, tz, warnings):
    it = empty_item()
    prefix, seq = luna_meta(primary)
    it["primary"] = rel(root, primary)
    it["companions"] = [rel(root, c) for c in companions]
    it["kind"] = "raw"
    it["subtype"] = prefix if prefix else "IMG"
    it["seq"] = seq
    t, src = item_time(primary, ex, False, tz, warnings)
    it["time"] = t
    it["time_end"] = None
    it["time_source"] = src
    fill_media_fields(it, primary, ex)
    return it


def make_video_item(root, primary, companions, ex, tz, warnings):
    it = empty_item()
    prefix, seq = luna_meta(primary)
    dur = duration_of(ex)
    it["primary"] = rel(root, primary)
    it["companions"] = [rel(root, c) for c in companions]
    it["kind"] = "video"
    it["subtype"] = prefix if prefix else "VID"
    it["seq"] = seq
    t, src = item_time(primary, ex, True, tz, warnings)
    it["time"] = t
    it["duration"] = dur
    it["time_end"] = (t + dur) if (t is not None and dur is not None) else None
    it["time_source"] = src
    fill_media_fields(it, primary, ex)
    return it


def items_from_group(root, files, index, tz, warnings, orphan_proxies, orphan_sidecars):
    buckets = {"image": [], "raw": [], "video": [], "proxy": [], "sidecar": []}
    for p in files:
        buckets[classify(p)].append(p)
    out = []
    images = sorted(buckets["image"], key=lambda p: (image_rank(p), p.name))
    raws = sorted(buckets["raw"], key=lambda p: p.name)
    videos = sorted(buckets["video"], key=lambda p: p.name)
    proxies = sorted(buckets["proxy"], key=lambda p: p.name)
    sidecars = sorted(buckets["sidecar"], key=lambda p: p.name)

    live_videos = []
    long_videos = []
    for v in videos:
        dur = duration_of(ex_of(index, v))
        if images and dur is not None and dur <= CONFIG["live_max_s"]:
            live_videos.append(v)
        else:
            long_videos.append(v)

    if images:
        primary = images[0]
        companions = images[1:] + raws + sidecars + live_videos
        ex = ex_of(index, primary)
        live_emb = is_live_embedded(ex)
        live = bool(live_videos) or live_emb
        out.append(make_photo_item(root, primary, companions, ex, tz, warnings, live, live_emb))
        sidecars = []
        raws = []
        proxies_left = list(proxies)
        for v in long_videos:
            v_comp = list(proxies_left)
            proxies_left = []
            out.append(make_video_item(root, v, v_comp, ex_of(index, v), tz, warnings))
        for pr in proxies_left:
            orphan_proxies.append(rel(root, pr))
        return out

    if raws:
        primary = raws[0]
        companions = raws[1:] + sidecars
        out.append(make_raw_item(root, primary, companions, ex_of(index, primary), tz, warnings))
        sidecars = []
        for v in long_videos:
            out.append(make_video_item(root, v, list(proxies), ex_of(index, v), tz, warnings))
            proxies = []
        for pr in proxies:
            orphan_proxies.append(rel(root, pr))
        return out

    if long_videos or videos:
        use = long_videos or videos
        attached = False
        for i, v in enumerate(use):
            v_comp = list(proxies) + (sidecars if i == 0 else [])
            if i == 0:
                sidecars = []
                proxies = []
                attached = True
            out.append(make_video_item(root, v, v_comp, ex_of(index, v), tz, warnings))
        for pr in proxies:
            orphan_proxies.append(rel(root, pr))
        for s in sidecars:
            orphan_sidecars.append(rel(root, s))
        return out

    for pr in proxies:
        orphan_proxies.append(rel(root, pr))
    for s in sidecars:
        orphan_sidecars.append(rel(root, s))
    return out


def assign_ids(items):
    pc = 0
    vc = 0
    for it in items:
        if it["kind"] == "video":
            vc += 1
            it["id"] = "V%04d" % vc
        else:
            pc += 1
            it["id"] = "P%04d" % pc


def fmt_ts(ts, tz):
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S")


def print_stats(items, meta, tz):
    photos = [it for it in items if it["kind"] in ("image", "raw")]
    videos = [it for it in items if it["kind"] == "video"]
    live_n = sum(1 for it in photos if it.get("live"))
    raw_paired = sum(
        1 for it in photos
        if it["kind"] == "image" and any(is_raw_path(c) for c in it.get("companions") or [])
    )
    raw_only = sum(1 for it in photos if it["kind"] == "raw")
    shot_n = sum(1 for it in photos if it.get("screenshot"))
    pano_n = sum(1 for it in photos if it.get("pano"))
    with_lrv = sum(1 for it in videos if any(is_proxy_path(c) for c in it.get("companions") or []))
    miss_lrv = [it for it in videos if not any(is_proxy_path(c) for c in it.get("companions") or [])]
    orphans = meta.get("orphan_proxies") or []

    print("照片 %d（实况 %d / RAW 配对 %d / 仅 RAW %d / 截图 %d / 全景 %d）" % (
        len(photos), live_n, raw_paired, raw_only, shot_n, pano_n))
    print("视频 %d（有 LRV %d / 缺 LRV %d）" % (len(videos), with_lrv, len(miss_lrv)))
    if miss_lrv:
        print("  缺 LRV: " + ", ".join(Path(it["primary"]).name for it in miss_lrv))
    print("孤儿 LRV %d%s" % (
        len(orphans),
        ("（%s）" % ", ".join(Path(x).name for x in orphans)) if orphans else "",
    ))

    times = [it["time"] for it in items if it.get("time") is not None]
    if times:
        print("时间跨度 %s → %s" % (fmt_ts(min(times), tz), fmt_ts(max(times), tz)))
    scenes = sorted({it.get("scene") for it in items if it.get("scene")})
    print("场景数 %d" % len(scenes))

    by_dev = defaultdict(list)
    for it in items:
        by_dev[it.get("device") or "(unknown)"].append(it)
    print("设备 %d 台" % len(by_dev))
    for dev, rows in sorted(by_dev.items()):
        ts = [it["time"] for it in rows if it.get("time") is not None]
        if ts:
            print("  %s: %s → %s (%d)" % (dev, fmt_ts(min(ts), tz), fmt_ts(max(ts), tz), len(rows)))
        else:
            print("  %s: (%d)" % (dev, len(rows)))

    srcs = Counter(it.get("time_source") for it in items)
    print("time_source: " + ", ".join("%s %d" % (k, n) for k, n in srcs.most_common()))
    print("时间警告 %d" % len(meta.get("time_warnings") or []))

    zb = Counter(it.get("zoom_bucket") for it in photos if it.get("zoom_bucket"))
    print("zoom_bucket: " + (", ".join("%s %d" % (k, n) for k, n in zb.most_common()) if zb else "(none)"))
    f35s = [it["focal35"] for it in photos if it.get("focal35") is not None]
    null_n = len(photos) - len(f35s)
    if f35s:
        print("focal35 min/median/max: %.1f / %.1f / %.1f（null %d）" % (
            min(f35s), statistics.median(f35s), max(f35s), null_n))
    else:
        print("focal35: (none)（null %d）" % null_n)


def maybe_tz_hint(warnings):
    if len(warnings) < 3:
        return
    buckets = Counter(int(round(off / 900.0)) * 900 for _name, off in warnings)
    top_n = buckets.most_common(1)[0][1]
    if top_n / float(len(warnings)) >= 0.8:
        print("疑似电脑时区与拍摄地时区不同，请在 CONFIG 设置 local_tz = 拍摄地时区（如 +09:00）后重跑")


def main():
    require_bins("exiftool")
    if len(sys.argv) < 2:
        sys.exit("用法: scan.py <目标目录>")
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        sys.exit("不是目录: " + str(root))

    tz = get_tz()
    index = run_exiftool(root)
    files = list(iter_files(root))
    groups = defaultdict(list)
    for p in files:
        groups[group_key(root, p)].append(p)
    group_lists = merge_groups(groups, index, root)

    warnings = []
    orphan_proxies = []
    orphan_sidecars = []
    items = []
    for g in group_lists:
        items.extend(items_from_group(root, g, index, tz, warnings, orphan_proxies, orphan_sidecars))

    assign_scenes(items)
    assign_ids(items)

    meta = {
        "file_count": len(files),
        "local_tz": CONFIG["local_tz"] or tz_offset_str(tz),
        "orphan_proxies": orphan_proxies,
        "orphan_sidecars": orphan_sidecars,
        "time_warnings": [{"file": n, "offset_s": o} for n, o in warnings],
    }
    out = save_report(root, "scan.json", meta, items)
    print_stats(items, meta, tz)
    maybe_tz_hint(warnings)
    print("→ " + str(out))


if __name__ == "__main__":
    main()
