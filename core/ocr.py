"""
Reconocimiento de texto (OCR) en español e inglés.

- Android: Google ML Kit Text Recognition (en el dispositivo, sin internet).
- Escritorio: EasyOCR si está instalado; si no, Tesseract (pytesseract).
"""
import os

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ


class OCRUnavailable(Exception):
    pass


_backend = None
_J = {}   # clases Java cargadas en el hilo principal


def preload():
    """En Android, las clases de librerías de la app (ML Kit, Play Services) solo
    se pueden cargar desde el hilo principal: se cargan aquí al iniciar."""
    if not IS_ANDROID:
        return
    from jnius import autoclass
    names = {
        "BitmapFactory": "android.graphics.BitmapFactory",
        "InputImage": "com.google.mlkit.vision.common.InputImage",
        "TextRecognition": "com.google.mlkit.vision.text.TextRecognition",
        "Options": "com.google.mlkit.vision.text.latin.TextRecognizerOptions",
    }
    for key, name in names.items():
        try:
            _J[key] = autoclass(name)
        except Exception as e:
            print("OCR preload: no se pudo cargar", name, e)
    try:
        # El reconocedor también se crea en el hilo principal y se reutiliza
        _J["recognizer"] = _J["TextRecognition"].getClient(_J["Options"].DEFAULT_OPTIONS)
    except Exception as e:
        print("OCR preload: no se pudo crear el reconocedor", e)


def _detect_backend():
    global _backend
    if _backend is not None:
        return _backend
    if IS_ANDROID:
        if not _J:
            preload()
        _backend = "mlkit" if "recognizer" in _J else ""
    else:
        _backend = ""
        try:
            import easyocr  # noqa: F401  (preferido en PC si está instalado)
            _backend = "easyocr"
        except Exception:
            try:
                import pytesseract
                pytesseract.get_tesseract_version()
                _backend = "tesseract"
            except Exception:
                pass
    return _backend


def available():
    return bool(_detect_backend())


def recognize(path):
    """Devuelve el texto de la imagen. Bloqueante: llamar desde un hilo."""
    backend = _detect_backend()
    if backend == "mlkit":
        return _mlkit(path)
    if backend == "easyocr":
        return _easyocr(path)
    if backend == "tesseract":
        return _tesseract(path)
    raise OCRUnavailable("OCR no disponible en este dispositivo")


# --------------------------------------------------------------------------- #
def _mlkit(path, timeout=90):
    """
    Todas las llamadas a ML Kit se hacen en el hilo principal (en Android, las
    clases de la app no se pueden cargar desde otros hilos). Este hilo solo
    espera el resultado, así que la interfaz no se congela.
    """
    import threading
    from kivy.clock import Clock

    if threading.current_thread() is threading.main_thread():
        raise RuntimeError("recognize() debe llamarse desde un hilo secundario")
    box = {}
    done = threading.Event()

    def finish(err=None, text=None):
        bmp = box.pop("bmp", None)
        if bmp is not None:
            try:
                bmp.recycle()
            except Exception:
                pass
        box["err"], box["text"] = err, text
        done.set()

    def start(dt):
        try:
            bmp = _J["BitmapFactory"].decodeFile(path)
            if bmp is None:
                raise IOError("No se pudo abrir la imagen")
            box["bmp"] = bmp
            box["task"] = _J["recognizer"].process(_J["InputImage"].fromBitmap(bmp, 0))
            Clock.schedule_once(poll, 0.05)
        except Exception as e:
            finish(err=e)

    def poll(dt):
        try:
            task = box["task"]
            if not task.isComplete():
                Clock.schedule_once(poll, 0.08)
                return
            if not task.isSuccessful():
                raise RuntimeError("ML Kit: %s" % task.getException())
            result = task.getResult()
            lines = []
            blocks = result.getTextBlocks()
            for i in range(blocks.size()):
                blines = blocks.get(i).getLines()
                for j in range(blines.size()):
                    lines.append(blines.get(j).getText())
                lines.append("")
            finish(text="\n".join(lines).strip())
        except Exception as e:
            finish(err=e)

    Clock.schedule_once(start, 0)
    if not done.wait(timeout):
        raise RuntimeError("El reconocimiento de texto tardó demasiado")
    if box["err"] is not None:
        raise box["err"]
    return box["text"] or ""


_easy_reader = None


def _easyocr(path):
    """EasyOCR (solo PC: requiere PyTorch, que no existe para Android)."""
    global _easy_reader
    import easyocr
    if _easy_reader is None:
        _easy_reader = easyocr.Reader(["es", "en"], gpu=False)
    # paragraph=True agrupa en líneas/párrafos en orden de lectura
    return "\n".join(_easy_reader.readtext(path, detail=0, paragraph=True)).strip()


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
