# scripts/faces.py
"""闭眼检测：YuNet 找脸 + MediaPipe FaceLandmarker blendshape。cv2 / mediapipe 延迟导入。"""
import time
from pathlib import Path

from PIL import Image

from common import CONFIG

try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS

SKILL_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = SKILL_DIR / "models"
YUNET_NAME = "face_detection_yunet_2023mar.onnx"
LANDMARKER_NAME = "face_landmarker.task"

LEFT_EAR_IDX = (33, 160, 158, 133, 153, 144)
RIGHT_EAR_IDX = (362, 385, 387, 263, 373, 380)
NOSE_TIP = 1
LEFT_EYE_OUTER = 33
RIGHT_EYE_OUTER = 263

_warned = False
_yn = None
_yn_size = None
_landmarker = None
_face_n = 0
_face_s = 0.0


def model_paths():
    return MODEL_DIR / YUNET_NAME, MODEL_DIR / LANDMARKER_NAME


def models_ok():
    a, b = model_paths()
    return a.is_file() and b.is_file()


def warn_missing_models():
    global _warned
    if _warned:
        return
    _warned = True
    print("警告: 缺少人脸模型（models/%s 或 models/%s），faces 字段为 null，其余分析照常。运行 scripts/fetch_models.sh 下载。"
          % (YUNET_NAME, LANDMARKER_NAME))


def face_timing():
    """(张数, 总秒, 每张毫秒)。"""
    ms = (_face_s / _face_n * 1000.0) if _face_n else 0.0
    return _face_n, _face_s, ms


def _null_fields():
    return {
        "faces_n": None,
        "subject_faces_n": None,
        "eyes_closed_n": None,
        "eyes_half_n": None,
        "subject_open_n": None,
        "faces": None,
        "faces_err": None,
    }


def empty_fields():
    return {
        "faces_n": 0,
        "subject_faces_n": 0,
        "eyes_closed_n": 0,
        "eyes_half_n": 0,
        "subject_open_n": 0,
        "faces": [],
        "faces_err": None,
    }


def _bs(cats, name):
    if not cats:
        return None
    for c in cats:
        if getattr(c, "category_name", None) == name:
            return round(float(c.score), 4)
    return None


def _pt(lms, idx, w, h):
    p = lms[idx]
    return (float(p.x) * w, float(p.y) * h)


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _ear(lms, idx, w, h):
    try:
        p1, p2, p3, p4, p5, p6 = [_pt(lms, i, w, h) for i in idx]
    except Exception:
        return None
    horiz = _dist(p1, p4)
    if horiz <= 1e-6:
        return None
    return round((_dist(p2, p6) + _dist(p3, p5)) / (2.0 * horiz), 4)


def _yaw(lms, w, h):
    try:
        nose = _pt(lms, NOSE_TIP, w, h)
        le = _pt(lms, LEFT_EYE_OUTER, w, h)
        re = _pt(lms, RIGHT_EYE_OUTER, w, h)
    except Exception:
        return None
    mid_x = (le[0] + re[0]) / 2.0
    half = abs(re[0] - le[0]) / 2.0
    if half <= 1e-6:
        return None
    return round((nose[0] - mid_x) / half, 3)


def _sunglasses(rgb, lms, w, h):
    """眼区又暗又平 → 墨镜/遮挡，不猜开闭。"""
    import numpy as np
    xs, ys = [], []
    for i in LEFT_EAR_IDX + RIGHT_EAR_IDX:
        try:
            x, y = _pt(lms, i, w, h)
        except Exception:
            continue
        xs.append(x)
        ys.append(y)
    if len(xs) < 8:
        return False
    x0 = max(0, int(min(xs) - 2))
    x1 = min(w, int(max(xs) + 2))
    y0 = max(0, int(min(ys) - 2))
    y1 = min(h, int(max(ys) + 2))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    patch = np.asarray(rgb)[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    gray = patch.mean(axis=2)
    return float(gray.mean()) < 22.0 and float(gray.std()) < 18.0


def _get_yn(cv2, w, h):
    global _yn, _yn_size
    path, _ = model_paths()
    if _yn is None:
        _yn = cv2.FaceDetectorYN.create(str(path), "", (w, h),
                                        float(CONFIG.get("eye_score", 0.7)),
                                        0.3, 5000)
        _yn_size = None
    if _yn_size != (w, h):
        _yn.setInputSize((w, h))
        _yn_size = (w, h)
    return _yn


def _get_landmarker():
    global _landmarker
    if _landmarker is not None:
        return _landmarker
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    _, path = model_paths()
    opts = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(path)),
        output_face_blendshapes=True,
        num_faces=3,
        running_mode=vision.RunningMode.IMAGE,
    )
    _landmarker = vision.FaceLandmarker.create_from_options(opts)
    return _landmarker


def _eye_state(face, img_short):
    t_closed = float(CONFIG.get("eye_t_closed", 0.5))
    t_half = float(CONFIG.get("eye_t_half", 0.35))
    yaw_lim = float(CONFIG.get("eye_yaw_unsure", 0.55))
    hmin = float(CONFIG.get("eye_h_min_frac", 0.03))
    smile_t = float(CONFIG.get("eye_smile_squint", 0.4))
    note = None
    h_px = face.get("h_px") or 0
    if h_px < img_short * hmin:
        return "unsure", note
    yaw = face.get("yaw")
    if yaw is None or abs(yaw) > yaw_lim:
        return "unsure", note
    if face.get("blink_l") is None or face.get("blink_r") is None:
        return "unsure", note
    bl, br = face["blink_l"], face["blink_r"]
    if bl >= t_closed and br >= t_closed:
        state = "closed"
    elif bl >= t_closed or br >= t_closed:
        state = "half"
    elif bl >= t_half and br >= t_half:
        state = "half"
    else:
        state = "open"
    smile = face.get("smile") or 0.0
    squint = face.get("squint") or 0.0
    if state == "closed" and smile >= smile_t and squint >= smile_t:
        state = "half"
        note = "smile_squint"
    elif state == "half" and smile >= smile_t and squint >= smile_t:
        state = "open"
        note = "smile_squint"
    return state, note


def analyze_faces(pil_rgb):
    """对已转正的 RGB PIL 图检测人脸。返回 (faces, err)。
    缺模型：(None, None)；出错：(None, 错误类型)；正常：(list, None)。"""
    global _face_n, _face_s
    if not models_ok():
        warn_missing_models()
        return None, None
    t0 = time.time()
    err = None
    faces = None
    try:
        faces = _analyze(pil_rgb)
    except Exception as e:
        faces = None
        err = type(e).__name__
    _face_n += 1
    _face_s += time.time() - t0
    return faces, err


def _pick_face(result, crop, orig, cw, ch):
    """在最多 3 张 landmarker 脸里，选关键点中心落在 YuNet 原框内、且离裁切中心最近的一张。"""
    if not result or not result.face_landmarks:
        return None
    x0, y0, x1, y1 = crop
    ox, oy, obw, obh = orig
    ccx = (x0 + x1) / 2.0
    ccy = (y0 + y1) / 2.0
    best = None
    best_d = None
    n_lms = len(result.face_landmarks)
    for i in range(n_lms):
        lms = result.face_landmarks[i]
        if not lms:
            continue
        sx = 0.0
        sy = 0.0
        n = 0
        for p in lms:
            sx += float(p.x) * cw
            sy += float(p.y) * ch
            n += 1
        if n == 0:
            continue
        mx = x0 + sx / n
        my = y0 + sy / n
        if mx < ox or mx > ox + obw or my < oy or my > oy + obh:
            continue
        d = (mx - ccx) ** 2 + (my - ccy) ** 2
        if best_d is None or d < best_d:
            best_d = d
            best = i
    return best


def _crop_xy(ox, oy, obw, obh, expand, ow, oh):
    cx = ox + obw / 2.0
    cy = oy + obh / 2.0
    nw = obw * (1.0 + expand)
    nh = obh * (1.0 + expand)
    x0 = int(round(cx - nw / 2.0))
    y0 = int(round(cy - nh / 2.0))
    x1 = int(round(cx + nw / 2.0))
    y1 = int(round(cy + nh / 2.0))
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(ow, x1)
    y1 = min(oh, y1)
    return x0, y0, x1, y1


def _run_landmarker(landmarker, mp, np, img, rec, orig, expand, ow, oh, img_short):
    """成功填充 rec 并返回 True。"""
    ox, oy, obw, obh = orig
    x0, y0, x1, y1 = _crop_xy(ox, oy, obw, obh, expand, ow, oh)
    cw, ch = x1 - x0, y1 - y0
    if cw < 4 or ch < 4:
        return False
    crop = img.crop((x0, y0, x1, y1))
    send = crop
    short = min(cw, ch)
    up_min = int(CONFIG.get("eye_upscale_min", 256))
    if short < up_min:
        sc = up_min / float(short)
        send = crop.resize((max(1, int(round(cw * sc))), max(1, int(round(ch * sc)))), _LANCZOS)
    rgb = np.array(send)
    if rgb.dtype != np.uint8:
        rgb = rgb.astype("uint8")
    if not rgb.flags["C_CONTIGUOUS"]:
        rgb = np.ascontiguousarray(rgb)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_image)
    idx = _pick_face(result, (x0, y0, x1, y1), orig, cw, ch)
    if idx is None:
        return False
    lms = result.face_landmarks[idx]
    cats = result.face_blendshapes[idx] if result.face_blendshapes else []
    rec["blink_l"] = _bs(cats, "eyeBlinkLeft")
    rec["blink_r"] = _bs(cats, "eyeBlinkRight")
    sl = _bs(cats, "eyeSquintLeft")
    sr = _bs(cats, "eyeSquintRight")
    rec["squint"] = None if sl is None and sr is None else round(max(sl or 0.0, sr or 0.0), 4)
    ml = _bs(cats, "mouthSmileLeft")
    mr = _bs(cats, "mouthSmileRight")
    rec["smile"] = None if ml is None and mr is None else round(max(ml or 0.0, mr or 0.0), 4)
    rec["ear_l"] = _ear(lms, LEFT_EAR_IDX, cw, ch)
    rec["ear_r"] = _ear(lms, RIGHT_EAR_IDX, cw, ch)
    rec["yaw"] = _yaw(lms, cw, ch)
    if _sunglasses(crop, lms, cw, ch):
        rec["eye_state"] = "unsure"
        rec["note"] = "occluded"
        return True
    state, note = _eye_state(rec, img_short)
    rec["eye_state"] = state
    if note:
        rec["note"] = note
    return True


def _analyze(pil_rgb):
    import cv2
    import numpy as np
    import mediapipe as mp

    img = pil_rgb
    if img.mode != "RGB":
        img = img.convert("RGB")
    ow, oh = img.size
    img_short = min(ow, oh)
    long_side = max(ow, oh)
    scale = 1.0
    det_img = img
    det_max = 1600
    if long_side > det_max:
        scale = det_max / float(long_side)
        nw = max(1, int(round(ow * scale)))
        nh = max(1, int(round(oh * scale)))
        det_img = img.resize((nw, nh), Image.BILINEAR)
    dw, dh = det_img.size
    bgr = np.array(det_img)[:, :, ::-1].copy()
    yn = _get_yn(cv2, dw, dh)
    _retval, raw = yn.detect(bgr)
    if raw is None or len(raw) == 0:
        return []

    expand = float(CONFIG.get("eye_expand", 0.4))
    expand2 = float(CONFIG.get("eye_expand_retry", 0.8))
    tiny_h = int(CONFIG.get("eye_tiny_h", 40))
    boxes = []
    for row in raw:
        x, y, bw, bh = [float(v) for v in row[:4]]
        ox = x / scale
        oy = y / scale
        obw = bw / scale
        obh = bh / scale
        boxes.append({
            "box": [int(round(ox)), int(round(oy)), int(round(obw)), int(round(obh))],
            "orig": (ox, oy, obw, obh),
            "h_px": int(round(obh)),
            "area": max(obw, 0) * max(obh, 0),
        })

    landmarker = _get_landmarker()
    out = []
    for b in boxes:
        rec = {
            "box": b["box"],
            "h_px": b["h_px"],
            "blink_l": None,
            "blink_r": None,
            "squint": None,
            "smile": None,
            "ear_l": None,
            "ear_r": None,
            "yaw": None,
            "rel_blink": None,
            "eye_state": "unsure",
            "subject": False,
        }
        if b["h_px"] < tiny_h:
            rec["note"] = "tiny"
            out.append(rec)
            continue
        ok = _run_landmarker(landmarker, mp, np, img, rec, b["orig"], expand, ow, oh, img_short)
        if not ok:
            ok = _run_landmarker(landmarker, mp, np, img, rec, b["orig"], expand2, ow, oh, img_short)
        if not ok:
            rec["note"] = "no_match"
        out.append(rec)

    if not out:
        return out
    max_area = max(b["area"] for b in boxes) or 1.0
    sub_frac = float(CONFIG.get("eye_subject_area_frac", 0.25))
    sub_h = float(CONFIG.get("eye_subject_h_frac", 0.08))
    for rec, b in zip(out, boxes):
        rec["subject"] = (b["area"] >= max_area * sub_frac) or (rec["h_px"] >= img_short * sub_h)
    return out


def fields_from_faces(faces, err=None):
    if faces is None:
        d = _null_fields()
        d["faces_err"] = err
        return d
    sub = [f for f in faces if f.get("subject")]
    return {
        "faces_n": len(faces),
        "subject_faces_n": len(sub),
        "eyes_closed_n": sum(1 for f in sub if f.get("eye_state") == "closed"),
        "eyes_half_n": sum(1 for f in sub if f.get("eye_state") == "half"),
        "subject_open_n": sum(1 for f in sub if f.get("eye_state") == "open"),
        "faces": faces,
        "faces_err": err,
    }
