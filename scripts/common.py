# scripts/common.py
import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

REPORT_DIR = "99_报告"
TIER_DIRS = {"00": "00_待删除", "01": "01_待复核", "02": "02_优秀", "03": "03_普通"}
SKIP_DIRS = set(TIER_DIRS.values()) | {REPORT_DIR}

IMG_EXTS = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
RAW_EXTS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mts", ".webm"}
PROXY_EXTS = {".lrv"}
SIDECAR_EXTS = {".xmp", ".aae", ".thm"}
ALL_EXTS = IMG_EXTS | RAW_EXTS | VIDEO_EXTS | PROXY_EXTS | SIDECAR_EXTS

DELETE_REASONS = {"模糊", "过曝", "欠曝", "歪斜", "闭眼", "遮挡", "误拍", "污损", "过短", "抖动"}

CONFIG = {
    "local_tz": None,          # None=本机时区；也可写 "+08:00"
    "scene_gap_min": 10,
    "scene_gps_m": 500,
    "dup_phash": 6,
    "burst_gap_s": 3,
    "burst_phash": 12,
    "cross_gap_s": 5,          # IMG 与 LIV 相似判定的时间窗
    "blur_ratio": 0.3,
    "min_group": 5,
    "entropy_min": 2.0,
    "clip_ratio": 0.40,
    "dark_mean": 10,
    "bright_mean": 245,
    "live_max_s": 4.0,
    "video_min_s": 2.0,
    "luna_lenses": {"w": 20, "t": 60},
    "zoom_wide_max": 40,
    "zoom_tele_max": 150,
    "analysis_px": 1280,
    "shake_hint": 3.0,         # 需用实拍素材校准
}


def nfc(s):
    return unicodedata.normalize("NFC", s)


def require_bins(*names):
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        sys.exit("缺少命令行工具: " + ", ".join(missing) + "（macOS: brew install exiftool ffmpeg）")


def require_heif():
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        sys.exit("缺少 pillow-heif：<SKILL_DIR>/.venv/bin/pip install pillow-heif")
    register_heif_opener()


def iter_files(root, exts=ALL_EXTS):
    root = Path(root)
    for p in sorted(root.rglob("*")):
        parts = p.relative_to(root).parts
        if any(x in SKIP_DIRS or x.startswith(".") for x in parts):
            continue
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def count_files(root):
    return sum(1 for _ in iter_files(root))


def rel(root, p):
    return nfc(Path(p).resolve().relative_to(Path(root).resolve()).as_posix())


def report_dir(root):
    d = Path(root) / REPORT_DIR
    d.mkdir(exist_ok=True)
    return d


def load_report(root, name):
    return json.loads((Path(root) / REPORT_DIR / name).read_text(encoding="utf-8"))


def save_report(root, name, meta, items):
    out = report_dir(root) / name
    out.write_text(json.dumps({"meta": meta, "items": items}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out


def ensure_scan(root):
    """scan.json 不存在或文件数变化时自动重扫。"""
    f = Path(root) / REPORT_DIR / "scan.json"
    stale = True
    if f.exists():
        meta = json.loads(f.read_text(encoding="utf-8")).get("meta", {})
        stale = meta.get("file_count") != count_files(root)
    if stale:
        subprocess.run([sys.executable, str(Path(__file__).parent / "scan.py"), str(root)], check=True)
    return load_report(root, "scan.json")


def hamming(a, b):
    return bin(a ^ b).count("1")


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a, b):
        self.p[self.find(b)] = self.find(a)

    def groups(self):
        g = {}
        for i in range(len(self.p)):
            g.setdefault(self.find(i), []).append(i)
        return [m for m in g.values() if len(m) > 1]


def group_median(items, key_funcs, metric, min_n):
    """按从细到粗的分组取中位数。返回 {primary: (median, level, n)}，level=len(key_funcs) 表示全局。"""
    import numpy as np
    vals = [it[metric] for it in items if it.get(metric) is not None]
    glob = float(np.median(vals)) if vals else None
    tables = []
    for kf in key_funcs:
        t = {}
        for it in items:
            if it.get(metric) is not None:
                t.setdefault(kf(it), []).append(it[metric])
        tables.append(t)
    out = {}
    for it in items:
        for lvl, (kf, t) in enumerate(zip(key_funcs, tables)):
            v = t.get(kf(it), [])
            if len(v) >= min_n:
                out[it["primary"]] = (float(np.median(v)), lvl, len(v))
                break
        else:
            out[it["primary"]] = (glob, len(key_funcs), len(vals))
    return out


def sharpness(gray, grid=4):
    import cv2
    import numpy as np
    h, w = gray.shape
    tiles = [cv2.Laplacian(gray[y * h // grid:(y + 1) * h // grid, x * w // grid:(x + 1) * w // grid],
                           cv2.CV_64F).var() for y in range(grid) for x in range(grid)]
    return float(np.percentile(tiles, 90))


def entropy(hist):
    import numpy as np
    nz = hist[hist > 0]
    return float(-(nz * np.log2(nz)).sum())


def gray_stats(rgb_img):
    import cv2
    import numpy as np
    g = cv2.cvtColor(np.array(rgb_img), cv2.COLOR_RGB2GRAY)
    hist = np.histogram(g, bins=256, range=(0, 256))[0] / g.size
    return {
        "blur_var": round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1),
        "sharp_p90": round(sharpness(g), 1),
        "brightness": round(float(g.mean()), 1),
        "clip_high": round(float(hist[250:].sum()), 3),
        "clip_low": round(float(hist[:5].sum()), 3),
        "entropy": round(entropy(hist), 2),
    }


def contact_sheet(images, labels, out_path, cols=3, cell=400):
    from PIL import Image, ImageDraw
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for i, (im, lab) in enumerate(zip(images, labels)):
        t = im.copy()
        t.thumbnail((cell, cell))
        x, y = (i % cols) * cell, (i // cols) * cell
        sheet.paste(t, (x + (cell - t.width) // 2, y + (cell - t.height) // 2))
        d.rectangle([x, y, x + cell - 1, y + 18], fill=(0, 0, 0))
        d.text((x + 4, y + 3), lab, fill=(255, 255, 0))
    sheet.save(out_path, quality=85)
