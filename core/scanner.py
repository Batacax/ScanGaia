"""
Núcleo de visión por computadora del escáner.

- detect_document(): encuentra los 4 bordes de la hoja (varias estrategias + puntuación)
- warp(): corrige la perspectiva y endereza la hoja
- enhance(): filtros (Mejorado, Gris, B/N, Nítido) con eliminación de sombras
"""
import cv2
import numpy as np

MAX_SIDE_OUTPUT = 3000      # resolución máxima de la página recortada
DETECT_SIDE = 640           # resolución de trabajo para detección

FILTERS = [
    ("magic", "Mejorado"),
    ("original", "Original"),
    ("gray", "Gris"),
    ("bw", "B/N"),
    ("sharp", "Nítido"),
]


# --------------------------------------------------------------------------- #
# Utilidades geométricas
# --------------------------------------------------------------------------- #
def order_points(pts):
    """Ordena 4 puntos como: sup-izq, sup-der, inf-der, inf-izq."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    out = np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                    pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)
    if len({(round(float(x), 3), round(float(y), 3)) for x, y in out}) < 4:
        c = pts.mean(axis=0)
        ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
        pts = pts[np.argsort(ang)]
        out = np.roll(pts, -int(np.argmin(pts.sum(axis=1))), axis=0)
    return out


def full_quad(img):
    h, w = img.shape[:2]
    return np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)


def is_valid_quad(quad, min_area=100.0):
    q = np.asarray(quad, np.float32).reshape(4, 1, 2)
    return bool(cv2.isContourConvex(q)) and cv2.contourArea(q) > min_area


def _angles_ok(quad, lo=50, hi=130):
    q = np.asarray(quad, np.float32)
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        n = np.linalg.norm(v1) * np.linalg.norm(v2)
        if n == 0:
            return False
        ang = np.degrees(np.arccos(np.clip(np.dot(v1, v2) / n, -1, 1)))
        if not lo <= ang <= hi:
            return False
    return True


def resize_max(img, max_side):
    h, w = img.shape[:2]
    s = max_side / float(max(h, w))
    if s >= 1:
        return img, 1.0
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA), s


# --------------------------------------------------------------------------- #
# Detección de bordes
# --------------------------------------------------------------------------- #
def _auto_canny(gray, sigma=0.33):
    v = float(np.median(gray))
    lo = int(max(0, (1.0 - sigma) * v))
    hi = int(min(255, (1.0 + sigma) * v))
    return cv2.Canny(gray, lo, max(hi, lo + 10))


def _edge_maps(small):
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    # "Borra" el texto de la hoja para que solo queden los bordes del papel
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k, iterations=2)
    blur = cv2.GaussianBlur(closed, (5, 5), 0)
    kd = np.ones((3, 3), np.uint8)

    maps = [cv2.dilate(_auto_canny(blur), kd),
            cv2.dilate(cv2.Canny(blur, 20, 70), kd)]

    sat = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 1]
    sat = cv2.GaussianBlur(sat, (5, 5), 0)
    maps.append(cv2.dilate(_auto_canny(sat), kd))

    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    maps.append(cv2.morphologyEx(otsu, cv2.MORPH_OPEN, k))
    return maps


def _candidates(edge, img_area):
    cnts, _ = cv2.findContours(edge, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:10]
    for c in cnts:
        hull = cv2.convexHull(c)
        area = cv2.contourArea(hull)
        if area < 0.12 * img_area:
            break
        peri = cv2.arcLength(hull, True)
        for eps in (0.015, 0.02, 0.03, 0.045, 0.06):
            approx = cv2.approxPolyDP(hull, eps * peri, True)
            if len(approx) == 4:
                q = order_points(approx)
                if is_valid_quad(q) and _angles_ok(q):
                    yield q, area, "poly"
                break
        else:
            # Sin 4 vértices claros: rectángulo mínimo como respaldo
            rect = cv2.boxPoints(cv2.minAreaRect(hull))
            yield order_points(rect), area * 0.85, "rect"


def _refine_corners(gray, quad):
    """Ajuste sub-píxel de las esquinas sobre la imagen original."""
    try:
        pts = quad.reshape(-1, 1, 2).astype(np.float32).copy()
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
        win = max(3, int(min(gray.shape[:2]) * 0.006))
        refined = cv2.cornerSubPix(gray, pts, (win, win), (-1, -1), crit).reshape(4, 2)
        # Solo se acepta si no se movió demasiado
        if np.max(np.linalg.norm(refined - quad, axis=1)) < win * 2:
            return refined
    except cv2.error:
        pass
    return quad


def detect_document(img, refine=True):
    """
    Devuelve (quad, confianza 0..1). quad está en coordenadas de `img`,
    ordenado sup-izq, sup-der, inf-der, inf-izq. Si no encuentra nada,
    devuelve la imagen completa con confianza 0.
    """
    small, s = resize_max(img, DETECT_SIDE)
    h, w = small.shape[:2]
    img_area = float(h * w)
    border = 0.01 * max(h, w)

    cands = []
    for mi, edge in enumerate(_edge_maps(small)):
        for q, area, kind in _candidates(edge, img_area):
            # Descarta el marco de la propia foto (las 4 esquinas en el borde)
            touching = np.sum((q[:, 0] < border) | (q[:, 0] > w - border) |
                              (q[:, 1] < border) | (q[:, 1] > h - border))
            if touching >= 4:
                continue
            score = area / img_area * (0.8 if kind == "rect" else 1.0)
            cands.append((mi, q, score))

    # Consenso: un borde que aparece en varias estrategias es más confiable
    tol = 0.03 * max(h, w)
    best, best_score = None, 0.0
    for mi, q, score in cands:
        votes = {mj for mj, q2, _ in cands
                 if mj != mi and np.max(np.linalg.norm(q - q2, axis=1)) < tol}
        total = score * (1.0 + 0.25 * len(votes))
        if total > best_score:
            best, best_score = q, total
    if best is not None:
        # Promedia las detecciones coincidentes para mayor precisión
        close = [q2 for _, q2, _ in cands if np.max(np.linalg.norm(best - q2, axis=1)) < tol]
        best = np.mean(close, axis=0).astype(np.float32)
        best_score = min(best_score, 1.0) if len(close) > 1 else best_score * 0.8

    if best is None:
        return full_quad(img), 0.0

    quad = best / s
    H, W = img.shape[:2]
    quad[:, 0] = np.clip(quad[:, 0], 0, W - 1)
    quad[:, 1] = np.clip(quad[:, 1], 0, H - 1)
    if refine and s < 1:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        quad = _refine_corners(gray, quad)
    conf = float(min(1.0, best_score / 0.6))
    return order_points(quad), conf


# --------------------------------------------------------------------------- #
# Corrección de perspectiva
# --------------------------------------------------------------------------- #
def true_aspect(quad, img_w, img_h):
    """
    Proporción real ancho/alto de un rectángulo visto en perspectiva
    (Zhang & He, "Whiteboard scanning and image enhancement").
    Devuelve None si no se puede estimar de forma fiable.
    """
    tl, tr, br, bl = order_points(quad).astype(np.float64)
    c = np.array([img_w / 2.0, img_h / 2.0])
    m1, m2, m3, m4 = [np.append(p - c, 1.0) for p in (tl, tr, bl, br)]
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    except ZeroDivisionError:
        return None
    n2 = k2 * m2 - m1
    n3 = k3 * m3 - m1
    if not np.all(np.isfinite([k2, k3])):
        return None
    denom = n2[2] * n3[2]
    f2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / denom if abs(denom) > 1e-9 else -1
    if f2 > 0 and abs(n2[2]) > 1e-6 and abs(n3[2]) > 1e-6:
        f = np.sqrt(f2)
        Ai = np.diag([1 / f, 1 / f, 1.0])
        r2 = (Ai @ n2) @ (Ai @ n2) / ((Ai @ n3) @ (Ai @ n3))
    else:  # casi frontal: perspectiva afín
        r2 = (n2[0] ** 2 + n2[1] ** 2) / max(n3[0] ** 2 + n3[1] ** 2, 1e-9)
    r = float(np.sqrt(r2)) if r2 > 0 else None
    return r if r and np.isfinite(r) else None


def warp(img, quad, max_side=MAX_SIDE_OUTPUT):
    q = order_points(quad)
    tl, tr, br, bl = q
    width = max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))
    height = max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))
    ratio = true_aspect(q, img.shape[1], img.shape[0])
    naive = width / max(height, 1)
    if ratio and 0.6 < ratio / naive < 1.6:
        # Conserva la mayor resolución disponible y corrige la proporción
        if ratio >= naive:
            height = width / ratio
        else:
            width = height * ratio
    scale = min(1.0, max_side / max(width, height, 1))
    W, H = max(int(width * scale), 10), max(int(height * scale), 10)
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], np.float32)
    M = cv2.getPerspectiveTransform(q, dst)
    out = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)
    # Recorta un margen mínimo para no arrastrar restos del fondo en los bordes
    m = int(0.004 * min(W, H))
    return out[m:H - m, m:W - m] if m > 0 else out


def rotate90(img, clockwise=True):
    return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE if clockwise
                      else cv2.ROTATE_90_COUNTERCLOCKWISE)


# --------------------------------------------------------------------------- #
# Filtros de mejora
# --------------------------------------------------------------------------- #
def _odd(n):
    n = int(n)
    return n if n % 2 else n + 1


def _flatten(ch):
    """Divide por el fondo estimado: elimina sombras e iluminación desigual."""
    h, w = ch.shape[:2]
    k = _odd(max(h, w) / 45)
    small, s = resize_max(ch, 1000)
    bg = cv2.dilate(small, np.ones((7, 7), np.uint8))
    bg = cv2.medianBlur(bg, min(_odd(max(21, k * s)), 99))
    if s < 1:
        bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(ch, bg, scale=255)


def _levels(img, black=0.08, white=0.98):
    """Estira el contraste usando percentiles."""
    lo = np.percentile(img, black * 100)
    hi = np.percentile(img, white * 100)
    if hi - lo < 10:
        return img
    out = (img.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def _unsharp(img, amount=0.8, sigma=1.2):
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)


def enhance(img, mode="magic"):
    if mode == "original":
        return img.copy()

    if mode == "magic":
        out = cv2.merge([_flatten(c) for c in cv2.split(img)])
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = _levels(l, 0.02, 0.92)
        # Un poco más de saturación para sellos y firmas
        a = cv2.addWeighted(a, 1.15, np.full_like(a, 128), -0.15, 0)
        b = cv2.addWeighted(b, 1.15, np.full_like(b, 128), -0.15, 0)
        out = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        return _unsharp(out, 0.5, 1.0)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    if mode == "gray":
        g = _levels(_flatten(gray), 0.02, 0.95)
        g = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(g)
        return cv2.cvtColor(_unsharp(g, 0.5), cv2.COLOR_GRAY2BGR)

    if mode == "bw":
        g = _flatten(gray)
        g = cv2.fastNlMeansDenoising(g, None, h=7, templateWindowSize=7, searchWindowSize=15) \
            if max(g.shape) <= 2200 else cv2.GaussianBlur(g, (3, 3), 0)
        block = _odd(max(15, max(g.shape) / 60))
        bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, block, 12)
        # Combina con Otsu para no perder trazos gruesos
        _, otsu = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        bw = cv2.bitwise_and(bw, otsu)
        bw = cv2.medianBlur(bw, 3)
        return cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)

    if mode == "sharp":
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(l)
        out = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        return _unsharp(out, 1.0, 1.4)

    raise ValueError("Filtro desconocido: %s" % mode)


# --------------------------------------------------------------------------- #
# E/S
# --------------------------------------------------------------------------- #
def load_image(path):
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)   # respeta orientación EXIF
    if img is None:
        raise ValueError("No se pudo abrir la imagen")
    return img


def save_jpeg(img, path, quality=90, max_side=2480):
    img, _ = resize_max(img, max_side)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality,
                                         cv2.IMWRITE_JPEG_OPTIMIZE, 1])
    if not ok:
        raise IOError("No se pudo codificar la imagen")
    buf.tofile(path)
    return img.shape[1], img.shape[0]


def auto_process(img, mode="magic"):
    """Detección + recorte + filtro en un solo paso (importación por lotes)."""
    quad, conf = detect_document(img)
    warped = warp(img, quad) if conf > 0.25 else resize_max(img, MAX_SIDE_OUTPUT)[0]
    return warped, enhance(warped, mode)
