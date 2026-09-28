#!/usr/bin/env python3
"""旅行视频粗筛：元数据、抽帧质量、音量、缩略图拼图。只输出分析，不动文件。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

try:
    import cv2
    import imagehash
    import numpy as np
except ImportError:
    sys.exit("请先安装: pip install pillow imagehash opencv-python numpy")

EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mts", ".webm"}
SKIP_DIRS = {"00_待删除", "01_待复核", "02_优秀", "03_普通", "99_报告", "_video_analysis"}


def probe(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    ).stdout
    d = json.loads(out or "{}")
    dur = float(d.get("format", {}).get("duration", 0) or 0)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    return dur, v.get("width", 0), v.get("height", 0), a is not None


def mean_volume(path):
    r = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    return None


def sample_frames(path, dur, tmpdir, n=6):
    frames = []
    if dur <= 0:
        return frames
    for i in range(n):
        t = dur * (i + 0.5) / n
        fp = Path(tmpdir) / f"f{i}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "quiet",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(fp),
            ],
            capture_output=True,
        )
        if fp.exists():
            img = Image.open(fp).convert("RGB")
            img.thumbnail((800, 800))
            frames.append(img)
    return frames


def contact_sheet(frames, out_path, cols=3):
    if not frames:
        return
    w, h = frames[0].size
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, h * rows))
    for i, f in enumerate(frames):
        sheet.paste(f, ((i % cols) * w, (i // cols) * h))
    sheet.save(out_path, quality=85)


def frame_stats(img):
    g = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    blur = cv2.Laplacian(g, cv2.CV_64F).var()
    hist = np.histogram(g, bins=256, range=(0, 256))[0] / g.size
    return blur, float(np.mean(g)), float(hist[250:].sum()), float(hist[:5].sum())


def main(folder):
    folder = Path(folder)
    out_dir = folder / "_video_analysis"
    out_dir.mkdir(exist_ok=True)
    results = []

    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            dur, w, h, has_audio = probe(p)
            flags = []
            if dur < 2:
                flags.append("V1")
            vol = mean_volume(p) if has_audio else None

            with tempfile.TemporaryDirectory() as td:
                frames = sample_frames(p, dur, td)
                stats = [frame_stats(f) for f in frames]
                sheet = out_dir / f"{p.stem}_sheet.jpg"
                contact_sheet(frames, sheet)
                mid_phash = (
                    str(imagehash.phash(frames[len(frames) // 2])) if frames else None
                )

            blurs = []
            if stats:
                blurs = [s[0] for s in stats]
                brights = [s[1] for s in stats]
                hi = [s[2] for s in stats]
                lo = [s[3] for s in stats]
                if sum(b < 10 or b > 245 for b in brights) >= len(brights) * 0.6:
                    flags.append("V5")
                if sum(x > 0.4 for x in hi) >= len(hi) * 0.6:
                    flags.append("V4-high")
                if sum(x > 0.4 for x in lo) >= len(lo) * 0.6:
                    flags.append("V4-low")
            results.append(
                {
                    "file": str(p),
                    "duration": round(dur, 1),
                    "size": f"{w}x{h}",
                    "has_audio": has_audio,
                    "mean_volume_db": vol,
                    "median_blur": round(float(np.median(blurs)), 1) if stats else None,
                    "mid_phash": mid_phash,
                    "sheet": str(sheet),
                    "flags": flags,
                }
            )
        except Exception as e:
            results.append({"file": str(p), "error": str(e), "flags": []})

    for i, a in enumerate(results):
        for b in results[i + 1 :]:
            if a.get("mid_phash") and b.get("mid_phash") and abs(a["duration"] - b["duration"]) < a["duration"] * 0.2 + 1:
                if imagehash.hex_to_hash(a["mid_phash"]) - imagehash.hex_to_hash(b["mid_phash"]) <= 6:
                    if "V6" not in b["flags"]:
                        b["flags"].append("V6")

    blurs = [r["median_blur"] for r in results if r.get("median_blur")]
    if blurs:
        med = np.median(blurs)
        for r in results:
            if r.get("median_blur") and r["median_blur"] < med * 0.3 and "V3" not in r["flags"]:
                r["flags"].append("V3")

    out = folder / "_video_analysis.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    flagged = sum(1 for r in results if r.get("flags"))
    errors = sum(1 for r in results if "error" in r)
    print(f"分析完成: {len(results)} 段（命中 {flagged}，读取失败 {errors}） → {out}，拼图 → {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
