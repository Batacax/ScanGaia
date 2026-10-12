"""
Funciones dependientes de la plataforma (Android vía pyjnius / escritorio).

Todas las funciones con callback pueden llamarlo desde otro hilo:
quien las use debe reenviar el resultado al hilo principal (@mainthread).
"""
import os
import shutil
import subprocess
import sys
import time

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ

_REQ_PICK = 4101
_REQ_CAMERA = 4102

if IS_ANDROID:
    from jnius import autoclass, cast
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Intent = autoclass("android.content.Intent")
    ContentValues = autoclass("android.content.ContentValues")
    MediaStore = autoclass("android.provider.MediaStore")
    MediaColumns = autoclass("android.provider.MediaStore$MediaColumns")
    Environment = autoclass("android.os.Environment")
    JFile = autoclass("java.io.File")
    JString = autoclass("java.lang.String")


def _activity():
    return PythonActivity.mActivity


def cache_dir():
    if IS_ANDROID:
        return _activity().getCacheDir().getAbsolutePath()
    d = os.path.join(os.path.expanduser("~"), ".cache", "scangaia")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Permisos y cámara
# --------------------------------------------------------------------------- #
def request_camera(callback):
    if not IS_ANDROID:
        callback(True)
        return
    from android.permissions import request_permissions, check_permission, Permission
    if check_permission(Permission.CAMERA):
        callback(True)
        return
    request_permissions([Permission.CAMERA],
                        lambda perms, grants: callback(bool(grants and all(grants))))


def camera_orientation(index=0):
    """Grados que hay que girar (horario) el cuadro de la cámara para verlo derecho."""
    if not IS_ANDROID:
        return 0
    try:
        Camera = autoclass("android.hardware.Camera")
        info = autoclass("android.hardware.Camera$CameraInfo")()
        Camera.getCameraInfo(index, info)
        return int(info.orientation)
    except Exception:
        return 90


# --------------------------------------------------------------------------- #
# Selección de archivos y cámara del sistema (Android)
# --------------------------------------------------------------------------- #
def _copy_uri(uri, dest):
    resolver = _activity().getContentResolver()
    stream = resolver.openInputStream(uri)
    try:
        Files = autoclass("java.nio.file.Files")
        Opt = autoclass("java.nio.file.StandardCopyOption")
        Files.copy(stream, JFile(dest).toPath(), [Opt.REPLACE_EXISTING])
    finally:
        stream.close()
    return dest


def pick_files(mime, multiple, callback):
    """Abre el selector del sistema. callback(lista_de_rutas). Solo Android."""
    from android import activity
    intent = Intent(Intent.ACTION_GET_CONTENT)
    intent.setType(mime)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, bool(multiple))
    ext = ".pdf" if "pdf" in mime else ".jpg"

    def on_result(request_code, result_code, data):
        if request_code != _REQ_PICK:
            return
        activity.unbind(on_activity_result=on_result)
        paths = []
        try:
            if result_code == -1 and data is not None:
                uris = []
                clip = data.getClipData()
                if clip is not None:
                    for i in range(clip.getItemCount()):
                        uris.append(clip.getItemAt(i).getUri())
                elif data.getData() is not None:
                    uris.append(data.getData())
                stamp = int(time.time() * 1000)
                for i, u in enumerate(uris):
                    paths.append(_copy_uri(u, os.path.join(cache_dir(), "in_%d_%d%s" % (stamp, i, ext))))
        finally:
            callback(paths)

    activity.bind(on_activity_result=on_result)
    _activity().startActivityForResult(Intent.createChooser(intent, JString("Seleccionar")), _REQ_PICK)


def capture_hd(callback):
    """Usa la app de cámara del sistema a resolución completa. callback(ruta o None)."""
    from android import activity
    resolver = _activity().getContentResolver()
    values = ContentValues()
    values.put(MediaColumns.DISPLAY_NAME, JString("scangaia_tmp_%d.jpg" % int(time.time())))
    values.put(MediaColumns.MIME_TYPE, JString("image/jpeg"))
    values.put(MediaColumns.RELATIVE_PATH, JString(Environment.DIRECTORY_PICTURES + "/ScanGaia"))
    uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
    intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
    intent.putExtra(MediaStore.EXTRA_OUTPUT, cast("android.os.Parcelable", uri))
    intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION)

    def on_result(request_code, result_code, data):
        if request_code != _REQ_CAMERA:
            return
        activity.unbind(on_activity_result=on_result)
        path = None
        try:
            if result_code == -1:
                path = _copy_uri(uri, os.path.join(cache_dir(), "hd_%d.jpg" % int(time.time())))
        except Exception:
            path = None
        finally:
            try:
                resolver.delete(uri, None, None)
            except Exception:
                pass
            callback(path)

    activity.bind(on_activity_result=on_result)
    try:
        _activity().startActivityForResult(intent, _REQ_CAMERA)
    except Exception:
        activity.unbind(on_activity_result=on_result)
        resolver.delete(uri, None, None)
        callback(None)


# --------------------------------------------------------------------------- #
# Exportar y compartir
# --------------------------------------------------------------------------- #
def _insert_download(path, name, mime):
    resolver = _activity().getContentResolver()
    values = ContentValues()
    values.put(MediaColumns.DISPLAY_NAME, JString(name))
    values.put(MediaColumns.MIME_TYPE, JString(mime))
    values.put(MediaColumns.RELATIVE_PATH, JString(Environment.DIRECTORY_DOWNLOADS + "/ScanGaia"))
    Downloads = autoclass("android.provider.MediaStore$Downloads")
    uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
    out = resolver.openOutputStream(uri)
    try:
        Files = autoclass("java.nio.file.Files")
        Files.copy(JFile(path).toPath(), out)
    finally:
        out.close()
    return uri


def save_to_downloads(path, name, mime="application/pdf"):
    """Copia el archivo a Descargas/ScanGaia. Devuelve un texto para mostrar."""
    if IS_ANDROID:
        _insert_download(path, name, mime)
        return "Guardado en Descargas/ScanGaia"
    dest_dir = os.path.join(os.path.expanduser("~"), "Downloads", "ScanGaia")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    shutil.copyfile(path, dest)
    return "Guardado en " + dest


_shared_uris = {}


def share_file(path, name, mime="application/pdf"):
    if IS_ANDROID:
        # El archivo se publica en Descargas (sin FileProvider) y se comparte su content://
        uri = _shared_uris.get(path) or _insert_download(path, name, mime)
        _shared_uris[path] = uri
        intent = Intent(Intent.ACTION_SEND)
        intent.setType(mime)
        intent.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", uri))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        chooser = Intent.createChooser(intent, JString("Compartir"))
        _activity().startActivity(chooser)
        return
    open_file(path)


def share_text(text):
    if IS_ANDROID:
        intent = Intent(Intent.ACTION_SEND)
        intent.setType("text/plain")
        intent.putExtra(Intent.EXTRA_TEXT, JString(text))
        _activity().startActivity(Intent.createChooser(intent, JString("Compartir texto")))


def open_file(path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --------------------------------------------------------------------------- #
# Importar PDF (rasterizar páginas a JPEG)
# --------------------------------------------------------------------------- #
def render_pdf(pdf_path, out_dir, width_px=1700):
    """Convierte cada página del PDF en JPEG. Devuelve la lista de rutas."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    if IS_ANDROID:
        PFD = autoclass("android.os.ParcelFileDescriptor")
        PdfRenderer = autoclass("android.graphics.pdf.PdfRenderer")
        PdfPage = autoclass("android.graphics.pdf.PdfRenderer$Page")
        Bitmap = autoclass("android.graphics.Bitmap")
        Config = autoclass("android.graphics.Bitmap$Config")
        Fmt = autoclass("android.graphics.Bitmap$CompressFormat")
        Canvas = autoclass("android.graphics.Canvas")
        Color = autoclass("android.graphics.Color")
        FOS = autoclass("java.io.FileOutputStream")
        fd = PFD.open(JFile(pdf_path), PFD.MODE_READ_ONLY)
        renderer = PdfRenderer(fd)
        try:
            for i in range(renderer.getPageCount()):
                page = renderer.openPage(i)
                w = width_px
                h = int(w * page.getHeight() / float(page.getWidth()))
                bmp = Bitmap.createBitmap(w, h, Config.ARGB_8888)
                Canvas(bmp).drawColor(Color.WHITE)
                page.render(bmp, None, None, PdfPage.RENDER_MODE_FOR_DISPLAY)
                page.close()
                p = os.path.join(out_dir, "pdf_%03d.jpg" % (i + 1))
                fos = FOS(p)
                bmp.compress(Fmt.JPEG, 92, fos)
                fos.close()
                bmp.recycle()
                paths.append(p)
        finally:
            renderer.close()
            fd.close()
        return paths

    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise RuntimeError("Instala 'pypdfium2' para importar PDF en escritorio")
    doc = pdfium.PdfDocument(pdf_path)
    for i in range(len(doc)):
        page = doc[i]
        scale = width_px / page.get_width()
        img = page.render(scale=scale).to_pil().convert("RGB")
        p = os.path.join(out_dir, "pdf_%03d.jpg" % (i + 1))
        img.save(p, quality=92)
        paths.append(p)
    return paths
