"""
Reconocimiento de texto (OCR) en español e inglés.

- Android: Google ML Kit Text Recognition (en el dispositivo, sin internet).
- Escritorio: Tesseract (pytesseract) si está instalado.
"""
import os

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ


class OCRUnavailable(Exception):
    pass


_backend = None


def _detect_backend():
    global _backend
    if _backend is not None:
        return _backend
    if IS_ANDROID:
        try:
            from jnius import autoclass
            autoclass("com.google.mlkit.vision.text.TextRecognition")
            _backend = "mlkit"
        except Exception:
            _backend = ""
    else:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            _backend = "tesseract"
        except Exception:
            _backend = ""
    return _backend


def available():
    return bool(_detect_backend())


def recognize(path):
    """Devuelve el texto de la imagen. Bloqueante: llamar desde un hilo."""
    backend = _detect_backend()
    if backend == "mlkit":
        return _mlkit(path)
    if backend == "tesseract":
        return _tesseract(path)
    raise OCRUnavailable("OCR no disponible en este dispositivo")


# --------------------------------------------------------------------------- #
def _mlkit(path):
    from jnius import autoclass
    BitmapFactory = autoclass("android.graphics.BitmapFactory")
    InputImage = autoclass("com.google.mlkit.vision.common.InputImage")
    TextRecognition = autoclass("com.google.mlkit.vision.text.TextRecognition")
    Options = autoclass("com.google.mlkit.vision.text.latin.TextRecognizerOptions")
    Tasks = autoclass("com.google.android.gms.tasks.Tasks")

    bmp = BitmapFactory.decodeFile(path)
    if bmp is None:
        raise IOError("No se pudo abrir la imagen")
    recognizer = TextRecognition.getClient(Options.DEFAULT_OPTIONS)
    try:
        task = recognizer.process(InputImage.fromBitmap(bmp, 0))
        result = getattr(Tasks, "await")(task)   # 'await' es palabra reservada en Python
        # Reconstruye por bloques/líneas para conservar el orden de lectura
        lines = []
        blocks = result.getTextBlocks()
        for i in range(blocks.size()):
            block = blocks.get(i)
            blines = block.getLines()
            for j in range(blines.size()):
                lines.append(blines.get(j).getText())
            lines.append("")
        return "\n".join(lines).strip()
    finally:
        recognizer.close()
        bmp.recycle()


def _tesseract(path):
    import cv2
    import numpy as np
    import pytesseract
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise IOError("No se pudo abrir la imagen")
    # Tesseract rinde mejor con ~300 ppp: escala páginas pequeñas
    if max(img.shape) < 2000:
        s = 2000.0 / max(img.shape)
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    langs = pytesseract.get_languages(config="")
    lang = "+".join(l for l in ("spa", "eng") if l in langs) or "eng"
    return pytesseract.image_to_string(img, lang=lang, config="--oem 1 --psm 3").strip()
