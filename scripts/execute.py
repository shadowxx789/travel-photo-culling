#!/usr/bin/env python3
"""execute.py — 按 plan.json 预览/移动/撤销/清理。只用标准库 + common.py。"""
import json
import os
import re
import sys
import time
from pathlib import Path

from common import (DELETE_REASONS, REPORT_DIR, SKIP_DIRS, TIER_DIRS, is_proxy, nfc)

LOG_NAME = ".cull_log.jsonl"
CLEAN_LOG = ".cleanup_log.jsonl"
REASON_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff-]{1,20}$")
PHOTO_DUP = "D"
VIDEO_DUP = "VD"


def die(msg):
    sys.exit("错误: " + msg)


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die("找不到 %s" % path)
    except ValueError as e:
        die("%s 不是合法 JSON: %s" % (path, e))


def report_dir(root):
    return Path(root) / REPORT_DIR


def log_path(root):
    return report_dir(root) / LOG_NAME


def rel_posix(root, p):
    return nfc(Path(p).resolve().relative_to(Path(root).resolve()).as_posix())


# ---------- plan 校验 ----------

def dup_index(analysis, video):
    """primary -> (dup_group, suggested_keep)。"""
    out = {}
    for src in (analysis, video):
        for it in src.get("items", []):
            out[it["primary"]] = (it.get("dup_group"), it.get("suggested_keep"))
    return out


def validate_plan(root, plan, scan, analysis, video):
    errors = []
    items = scan.get("items", [])
    scan_map = {it["primary"]: it for it in items}
    orphans = set(scan.get("meta", {}).get("orphan_proxies") or [])
    orphan_sc = set(scan.get("meta", {}).get("orphan_sidecars") or [])
    dups = dup_index(analysis, video)
    plan_items = plan.get("items") or []

    # 规则 7：分析结果比 plan 新则拒绝
    plan_m = os.path.getmtime(Path(root) / REPORT_DIR / "plan.json")
    for name in ("scan.json", "analysis.json", "video_analysis.json"):
        f = report_dir(root) / name
        if f.exists() and os.path.getmtime(f) > plan_m:
            errors.append("分析结果比 plan 新，请重新生成 plan（%s）" % name)

    seen = set()
    for i, e in enumerate(plan_items):
        tag = "items[%d]" % i
        p = e.get("primary")
        if not p:
            errors.append("%s: primary 缺失" % tag)
            continue
        p = nfc(p)
        # 规则 3：proxy / 孤儿 LRV / sidecar 不能作 primary（先于成员检查，报错更准）
        if is_proxy(p) or p in orphans:
            errors.append("%s: proxy/孤儿 LRV 不能作为 primary: %s" % (tag, p))
        if p in orphan_sc or Path(p).suffix.lower() in (".xmp", ".aae", ".thm"):
            errors.append("%s: sidecar 不能作为 primary: %s" % (tag, p))
        if p not in scan_map:
            errors.append("%s: primary 不是 scan.json 条目: %s" % (tag, p))
            continue
        if p in seen:
            errors.append("%s: primary 重复出现: %s" % (tag, p))
        seen.add(p)
        # 规则 4
        tier = e.get("tier")
        if tier not in TIER_DIRS:
            errors.append("%s: tier 非法（应为 00/01/02/03）: %r" % (tag, tier))
            continue
        # 规则 5
        reason = e.get("reason") or ""
        if tier == "00":
            if reason not in DELETE_REASONS:
                errors.append("%s: tier 00 的 reason 必须属于 DELETE_REASONS: %r" % (tag, reason))
        elif tier == "01":
            if not reason.startswith("复核-"):
                errors.append("%s: tier 01 的 reason 必须以 '复核-' 开头: %r" % (tag, reason))
        if reason and not REASON_RE.match(reason):
            errors.append("%s: reason 含非法字符或超过 20 字: %r" % (tag, reason))
        # 规则 6
        if reason == "重复":
            keep = e.get("keep")
            if not keep:
                errors.append("%s: reason='重复' 必须填 keep" % tag)
            else:
                keep = nfc(keep)
                if keep not in scan_map:
                    errors.append("%s: keep 不是 scan.json 条目: %s" % (tag, keep))
                else:
                    g1 = dups.get(p, (None, None))[0]
                    g2 = dups.get(keep, (None, None))[0]
                    if g1 and g2:
                        k1 = VIDEO_DUP if g1.startswith(VIDEO_DUP) else PHOTO_DUP
                        k2 = VIDEO_DUP if g2.startswith(VIDEO_DUP) else PHOTO_DUP
                        if k1 != k2:
                            errors.append("%s: 照片 D 簇与视频 VD 簇不能互相引用（%s vs %s）"
                                          % (tag, g1, g2))
                        elif g1 != g2:
                            errors.append("%s: keep(%s) 与本条不在同一 dup_group（%s vs %s）"
                                          % (tag, keep, g1, g2))
                    else:
                        errors.append("%s: 本条或 keep 无 dup_group（%s vs %s）" % (tag, g1, g2))
                    for j, e2 in enumerate(plan_items):
                        if nfc(e2.get("primary") or "") == keep and e2.get("tier") == "00":
                            errors.append("%s: keep(%s) 本身在 tier 00（items[%d]）" % (tag, keep, j))

    # 规则 2：scan.json 每个条目都要在 plan 里
    missing = [it["primary"] for it in items if it["primary"] not in seen]
    for m in missing:
        errors.append("plan 缺少条目: %s" % m)

    # 规则 8：磁盘存在性
    for it in items:
        for f in [it["primary"]] + list(it.get("companions") or []):
            if not (Path(root) / f).exists():
                errors.append("磁盘上不存在: %s" % f)
    return errors


# ---------- 目标路径 ----------

def tier_prefix(tier, reason):
    if tier == "00":
        return reason + "__"
    if tier == "01":
        return reason.replace("复核-", "", 1) + "__"
    return ""


def add_suffix(name, suf):
    """在扩展名前加后缀；AppleDouble 的 ._ 保持前缀。"""
    if name.startswith("._"):
        base = name[2:]
        stem, dot, ext = base.partition(".")
        return "._" + stem + suf + (dot + ext if dot else "")
    stem, dot, ext = name.partition(".")
    return stem + suf + (dot + ext if dot else "")


def group_moves(root, entry, scan_map):
    """一个条目（primary+companions+各自 ._）的移动清单 [(src_rel, dst_rel)]，撞名整组加 _2。"""
    tier = entry["tier"]
    reason = entry.get("reason") or ""
    primary = nfc(entry["primary"])
    names = [primary] + list(scan_map[primary].get("companions") or [])
    pref = tier_prefix(tier, reason)
    tier_dir = TIER_DIRS[tier]
    suffix = ""
    while True:
        moves = []
        clash = False
        for src_rel in names:
            src = Path(root) / src_rel
            parent = src_rel.rsplit("/", 1)[0] if "/" in src_rel else ""
            base = tier_dir + "/" + (parent + "/" if parent else "")
            dst_rel = base + pref + add_suffix(src.name, suffix)
            moves.append((src_rel, dst_rel))
            if dst_rel != src_rel and (Path(root) / dst_rel).exists():
                clash = True
            ad = src.parent / ("._" + src.name)
            if ad.exists():
                ad_rel = rel_posix(root, ad)
                dst_ad = base + "._" + pref + add_suffix(src.name, suffix)
                moves.append((ad_rel, dst_ad))
                if dst_ad != ad_rel and (Path(root) / dst_ad).exists():
                    clash = True
        if not clash:
            return moves
        suffix = "_2" if suffix == "" else "_%d" % (int(suffix[1:]) + 1)


def build_plan_moves(root, plan, scan):
    scan_map = {it["primary"]: it for it in scan["items"]}
    out = []
    for entry in plan["items"]:
        out.extend(group_moves(root, entry, scan_map))
    return out


# ---------- 日志 ----------

def read_log(root):
    f = log_path(root)
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def log_append(root, rec):
    f = log_path(root)
    report_dir(root).mkdir(exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def open_batches(records):
    """返回未撤销的 batch_id 列表（按出现顺序）。"""
    state = {}
    for r in records:
        b = r.get("batch")
        if r.get("state") == "undone":
            state[b] = "undone"
        elif b not in state:
            state[b] = "open"
    return [b for b, s in state.items() if s == "open"]


def same_dev(root, src_rel):
    try:
        return os.stat(Path(root) / src_rel).st_dev == os.stat(Path(root)).st_dev
    except OSError:
        return True


# ---------- --apply ----------

def cmd_apply(root, scan):
    records = read_log(root)
    pending = open_batches(records)
    if pending:
        die("日志里还有没撤销的 batch（%s），请先执行 --undo" % ", ".join(pending))
    plan = load_json(report_dir(root) / "plan.json")
    analysis = load_json(report_dir(root) / "analysis.json")
    video = load_json(report_dir(root) / "video_analysis.json")
    errors = validate_plan(root, plan, scan, analysis, video)
    if errors:
        print("校验未通过，拒绝执行（%d 个错误）：" % len(errors))
        for e in errors:
            print("  " + e)
        return 1
    moves = build_plan_moves(root, plan, scan)
    for src_rel, dst_rel in moves:
        if not same_dev(root, src_rel):
            die("检测到跨设备移动（%s），拒绝执行" % src_rel)
    batch = "B%d" % int(time.time() * 1000)
    crash_after = int(os.environ.get("CULL_CRASH_AFTER") or 0)
    crash_mode = os.environ.get("CULL_CRASH_MODE") or ""
    n = 0
    auto_n = 0
    for seq, (src_rel, dst_rel) in enumerate(moves, 1):
        src = Path(root) / src_rel
        dst = Path(root) / dst_rel
        if src.name.startswith("._"):
            if not src.exists() and dst.exists():
                # 文件系统在主文件 rename 时已自动带走 ._，记 auto
                log_append(root, {"batch": batch, "seq": seq, "src": src_rel, "dst": dst_rel, "state": "auto"})
                auto_n += 1
                continue
            if not src.exists():
                continue  # 源和目标都没有：跳过
        if dst.exists():
            die("目标路径已存在，拒绝覆盖：%s" % dst_rel)
        log_append(root, {"batch": batch, "seq": seq, "src": src_rel, "dst": dst_rel, "state": "pending"})
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        n += 1
        if crash_after and n == crash_after and crash_mode == "before_done":
            os._exit(1)  # rename 完成、done 未写
        log_append(root, {"batch": batch, "seq": seq, "state": "done"})
        if crash_after and n == crash_after:
            os._exit(1)
    print("已移动 %d 个文件（batch %s%s）" % (
        n, batch, "，系统自动处理 ._ %d 个" % auto_n if auto_n else ""))
    return 0


# ---------- --undo ----------

def is_junk_dir_entry(name):
    return name == ".DS_Store" or name.startswith("._")


def clean_empty_dirs(root):
    """删空层级目录（只含 .DS_Store / ._ 的也算空），连同目录自身的 ._ 影子。"""
    removed = []
    for tier in sorted(TIER_DIRS.values()):
        base = Path(root) / tier
        if not base.exists():
            continue
        for d in sorted(base.rglob("*"), key=lambda p: -len(p.parts)):
            if not d.is_dir():
                continue
            entries = list(d.iterdir())
            if entries and all(is_junk_dir_entry(e.name) for e in entries):
                for e in entries:
                    e.unlink()
                d.rmdir()
                removed.append(str(d))
            elif not entries:
                d.rmdir()
                removed.append(str(d))
        if base.exists() and not any(base.iterdir()):
            base.rmdir()
            removed.append(str(base))
    # exFAT 会给目录生成 ._ 影子文件：目录删掉后影子一并清理
    for d in removed:
        shadow = Path(d).parent / ("._" + Path(d).name)
        if shadow.exists():
            shadow.unlink()
    return removed


def cmd_undo(root):
    records = read_log(root)
    open_b = open_batches(records)
    if not open_b:
        print("没有待撤销的 batch")
        return 0
    moved_back = 0
    auto_back = 0
    conflicts = []
    for batch in reversed(open_b):
        rows = [r for r in records if r.get("batch") == batch]
        # 先还原主文件（fskit 会随主文件自动带走 ._），再处理 ._ 记录
        mains = [x for x in rows if "src" in x and not Path(x["src"]).name.startswith("._")]
        ads = [x for x in rows if "src" in x and Path(x["src"]).name.startswith("._")]
        for r in sorted(mains, key=lambda x: -x["seq"]) + sorted(ads, key=lambda x: -x["seq"]):
            src = Path(root) / r["src"]
            dst = Path(root) / r["dst"]
            if r.get("state") == "auto":
                # 系统自动处理过的 ._：还原规则同 apply——已还原/都没有则跳过，绝不报冲突
                if dst.exists() and not src.exists():
                    src.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(dst, src)
                    auto_back += 1
                continue
            done = any(x.get("batch") == batch and x.get("seq") == r["seq"]
                       and x.get("state") == "done" for x in rows)
            if done:
                if dst.exists() and not src.exists():
                    src.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(dst, src)
                    moved_back += 1
                elif dst.exists() and src.exists():
                    conflicts.append("%s 与 %s 都存在" % (r["dst"], r["src"]))
                elif not dst.exists() and not src.exists():
                    conflicts.append("%s 与 %s 都不存在" % (r["dst"], r["src"]))
            else:  # pending：可能没移动过
                if dst.exists() and not src.exists():
                    src.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(dst, src)
                    moved_back += 1
                elif src.exists():
                    pass  # 没移动过，跳过
                else:
                    conflicts.append("%s 与 %s 都不存在" % (r["dst"], r["src"]))
        log_append(root, {"batch": batch, "state": "undone"})
    for d in clean_empty_dirs(root):
        print("已删除空目录 %s" % d)
    print("已还原 %d 个文件%s" % (moved_back, "（._ 自动还原 %d 个）" % auto_back if auto_back else ""))
    if conflicts:
        print("冲突 %d 个（未覆盖任何文件）：" % len(conflicts))
        for c in conflicts:
            print("  " + c)
    return 0


# ---------- --list ----------

def fmt_size(n):
    if n >= 1 << 20:
        return "%.1f MB" % (n / (1 << 20))
    return "%.1f KB" % (n / 1024.0) if n else "0 B"


def cmd_list(root, plan, scan):
    analysis = load_json(report_dir(root) / "analysis.json")
    video = load_json(report_dir(root) / "video_analysis.json")
    errors = validate_plan(root, plan, scan, analysis, video)
    if errors:
        print("校验未通过（%d 个错误）：" % len(errors))
        for e in errors:
            print("  " + e)
        return 1
    scan_map = {it["primary"]: it for it in scan["items"]}
    by_tier = {}
    for entry in plan["items"]:
        by_tier.setdefault(entry["tier"], []).append(entry)
    for tier in sorted(by_tier):
        entries = by_tier[tier]
        names = []
        size = 0
        reasons = {}
        print("== %s：条目 %d ==" % (TIER_DIRS[tier], len(entries)))
        for entry in entries:
            it = scan_map[nfc(entry["primary"])]
            files = [it["primary"]] + list(it.get("companions") or [])
            names.append((entry, it, files))
            for f in files:
                size += (Path(root) / f).stat().st_size
            r = entry.get("reason") or "(无)"
            reasons[r] = reasons.get(r, 0) + 1
        print("   文件 %d（含 companions），总大小 %s" % (sum(len(f) for _e, _i, f in names), fmt_size(size)))
        print("   reason: " + "、".join("%s %d" % (k, v) for k, v in sorted(reasons.items())))
        for entry, it, files in names[:10]:
            moves = group_moves(root, entry, scan_map)
            head = moves[0]
            extra = ""
            if it.get("slowmo_factor"):
                extra = "（慢动作 capture_s=%ss / duration=%ss）" % (it.get("capture_s"), it.get("duration"))
            print("   %s → %s%s" % (head[0], head[1], extra))
            for src_rel, dst_rel in moves[1:]:
                print("       + %s → %s" % (src_rel, dst_rel))
        if len(names) > 10:
            print("   …… 其余 %d 条省略" % (len(names) - 10))
    orphans = scan.get("meta", {}).get("orphan_proxies") or []
    if orphans:
        print("== 孤儿 LRV %d ==" % len(orphans))
        for o in orphans:
            print("   %s：原片缺失，已跳过，不做任何处理" % o)
    return 0


# ---------- --cleanup ----------

def cmd_cleanup(root, yes):
    targets = []
    for p in sorted(Path(root).rglob("._*")):
        if REPORT_DIR in p.parts:
            continue
        if not (p.parent / p.name[2:]).exists():
            targets.append(p)
    for d in sorted(Path(root).rglob("*"), key=lambda p: -len(p.parts)):
        if not d.is_dir() or REPORT_DIR in d.parts or d == Path(root):
            continue
        entries = list(d.iterdir())
        if not entries or all(is_junk_dir_entry(e.name) for e in entries):
            targets.append(d)
    if not targets:
        print("没有可清理的项目")
        return 0
    print("可清理 %d 项：" % len(targets))
    for t in targets:
        print("   " + rel_posix(root, t) + ("/" if t.is_dir() else ""))
    if not yes:
        print("（只列出。加 --yes 才真正删除）")
        return 0
    f = report_dir(root) / CLEAN_LOG
    report_dir(root).mkdir(exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        for t in targets:
            rec = {"time": time.time(), "path": rel_posix(root, t), "type": "dir" if t.is_dir() else "file"}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if t.is_dir():
                for e in t.iterdir():
                    e.unlink()
                t.rmdir()
            else:
                t.unlink()
        fh.flush()
        os.fsync(fh.fileno())
    print("已删除 %d 项（记录见 99_报告/%s）" % (len(targets), CLEAN_LOG))
    return 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if len(args) != 1:
        die("用法: execute.py <目标目录> --list | --apply | --undo | --cleanup [--yes]")
    root = Path(args[0]).resolve()
    if not root.is_dir():
        die("不是目录: %s" % root)
    yes = "--yes" in flags
    ops = [f for f in flags if f != "--yes"]
    if len(ops) != 1:
        die("用法: execute.py <目标目录> --list | --apply | --undo | --cleanup [--yes]")
    op = ops[0]
    scan = load_json(report_dir(root) / "scan.json")
    if op == "--list":
        plan = load_json(report_dir(root) / "plan.json")
        return cmd_list(root, plan, scan)
    if op == "--apply":
        return cmd_apply(root, scan)
    if op == "--undo":
        return cmd_undo(root)
    if op == "--cleanup":
        return cmd_cleanup(root, yes)
    die("未知操作: %s" % op)


if __name__ == "__main__":
    sys.exit(main())
