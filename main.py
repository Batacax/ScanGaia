"""
ScanGaia — escáner de documentos para celular, hecho en Python (Kivy + OpenCV).

Cámara con detección de bordes en vivo y captura automática, recorte preciso con
lupa, corrección de perspectiva, filtros de mejora, PDF de varias páginas con
texto buscable (OCR), carpetas, búsqueda y cuentas privadas.
"""
import datetime
import os
import shutil
import threading
import time
import traceback

import cv2
import numpy as np

from kivy.app import App
from kivy.animation import Animation
from kivy.clock import Clock, mainthread
from kivy.core.clipboard import Clipboard
from kivy.core.text import Label as CoreLabel
from kivy.core.window import Window
from kivy.event import EventDispatcher
from kivy.graphics import Color, Ellipse, Line, Mesh, Rectangle
from kivy.graphics.texture import Texture
from kivy.lang import Builder
from kivy.metrics import dp, sp
from kivy.properties import (BooleanProperty, ListProperty, NumericProperty,
                             ObjectProperty, StringProperty)
from kivy.uix.behaviors import ButtonBehavior, ToggleButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager, SlideTransition
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex as hexc

from core import ocr, pdfgen, scanner
from core import platform_utils as pu
from core.icons import IC
from core.storage import Store

BLUE = hexc("#1A6FF0")
L_TXT = hexc("#0F172A")
L_MUT = hexc("#64748B")
L_LINE = hexc("#E2E8F0")
L_CARD = hexc("#FFFFFF")
RED = hexc("#E5484D")
ICON_FONT = "assets/MaterialIcons-Regular.ttf"
APP_DIR = os.path.dirname(os.path.abspath(__file__))


# =========================================================================== #
# Utilidades
# =========================================================================== #
def np_to_texture(img):
    """Convierte una imagen BGR de OpenCV en textura de Kivy."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    rgba = cv2.cvtColor(cv2.flip(img, 0), cv2.COLOR_BGR2RGBA)
    tex = Texture.create(size=(w, h), colorfmt="rgba")
    tex.blit_buffer(rgba.tobytes(), colorfmt="rgba", bufferfmt="ubyte")
    return tex


def make_gradient(c1="#0A84FF", c2="#1E50D8"):
    a, b = np.array(hexc(c1)[:3]), np.array(hexc(c2)[:3])
    n = 64
    cols = np.array([a + (b - a) * i / (n - 1) for i in range(n)])
    buf = (np.concatenate([cols, np.ones((n, 1))], axis=1) * 255).astype(np.uint8)
    tex = Texture.create(size=(n, 1), colorfmt="rgba")
    tex.blit_buffer(buf.tobytes(), colorfmt="rgba", bufferfmt="ubyte")
    return tex


def fmt_date(ts):
    d = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if d.date() == today:
        return "Hoy, " + d.strftime("%H:%M")
    if d.date() == today - datetime.timedelta(days=1):
        return "Ayer, " + d.strftime("%H:%M")
    return d.strftime("%d/%m/%Y")


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1024.0


def safe_filename(title):
    keep = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
    return (keep or "documento")[:60]


def run_bg(work, done=None, busy=None, error_msg="Ocurrió un error"):
    """Ejecuta `work` en un hilo y llama `done(resultado)` en el hilo principal."""
    app = App.get_running_app()
    if busy:
        app.show_busy(busy)

    def finish(ok, result):
        if busy:
            app.hide_busy()
        if ok:
            if done:
                done(result)
        else:
            msg = str(result) if isinstance(result, (ValueError, RuntimeError,
                                                     ocr.OCRUnavailable)) else error_msg
            app.toast(msg)

    def thread():
        try:
            res, ok = work(), True
        except Exception as e:  # noqa
            traceback.print_exc()
            res, ok = e, False
        finally:
            if pu.IS_ANDROID:
                try:
                    import jnius
                    jnius.detach()
                except Exception:
                    pass
        Clock.schedule_once(lambda dt: finish(ok, res))

    threading.Thread(target=thread, daemon=True).start()


# =========================================================================== #
# Widgets base
# =========================================================================== #
class Icon(Label):
    pass


class IconButton(ButtonBehavior, Icon):
    bg_color = ListProperty([0, 0, 0, 0])


class RoundBox(BoxLayout):
    bg_color = ListProperty(L_CARD)
    border_color = ListProperty(L_LINE)
    shadow_color = ListProperty([0.06, 0.09, 0.16, 0.035])
    radius = NumericProperty(dp(18))
    border_width = NumericProperty(dp(1))
    texture = ObjectProperty(None, allownone=True)


class PressBox(ButtonBehavior, RoundBox):
    pass


class GradButton(ButtonBehavior, Label):
    grad = ObjectProperty(None, allownone=True)
    fg = ListProperty([1, 1, 1, 1])
    radius = NumericProperty(dp(28))
    glow = BooleanProperty(True)


class OutlineBtn(ButtonBehavior, Label):
    fg = ListProperty(L_TXT)
    line = ListProperty(L_LINE)
    bg = ListProperty(L_CARD)
    radius = NumericProperty(dp(28))


class Field(TextInput):
    dark = BooleanProperty(False)
    icon = StringProperty("")
    icon_tex = ObjectProperty(None, allownone=True)
    radius = NumericProperty(dp(16))

    def on_icon(self, *_):
        if not self.icon:
            self.icon_tex = None
            return
        lbl = CoreLabel(text=self.icon, font_name=ICON_FONT, font_size=sp(21))
        lbl.refresh()
        self.icon_tex = lbl.texture


class Chip(ToggleButtonBehavior, Label):
    fg = ListProperty(L_TXT)
    bg = ListProperty(L_CARD)
    line = ListProperty(L_LINE)


class SectionHeader(BoxLayout):
    title = StringProperty("")
    action = StringProperty("")
    __events__ = ("on_link",)

    def on_link(self):
        pass


class NavItem(ButtonBehavior, BoxLayout):
    icon = StringProperty("")
    text = StringProperty("")
    active = BooleanProperty(False)


class BottomNav(Widget):
    active = StringProperty("home")


class ScanFab(ButtonBehavior, Widget):
    pass


class Avatar(ButtonBehavior, Widget):
    pass


class Shutter(ButtonBehavior, Widget):
    pass


class FeatureCard(RoundBox):
    icon = StringProperty("")
    title = StringProperty("")
    desc = StringProperty("")


class QuickAction(PressBox):
    icon = StringProperty("")
    text = StringProperty("")


class HeroCard(ButtonBehavior, BoxLayout):
    pass


class DocRow(PressBox):
    title = StringProperty("")
    subtitle = StringProperty("")
    thumb = StringProperty("")
    doc_id = StringProperty("")
    __events__ = ("on_more",)

    def on_more(self):
        pass


class FolderCard(PressBox):
    title = StringProperty("")
    subtitle = StringProperty("")


class ToolRow(PressBox):
    icon = StringProperty("")
    title = StringProperty("")
    desc = StringProperty("")


class EmptyState(RoundBox):
    icon = StringProperty(IC["scan"])
    title = StringProperty("")
    desc = StringProperty("")


class PageThumb(RoundBox):
    texture_img = ObjectProperty(None, allownone=True)
    number = NumericProperty(1)


class BarAction(ButtonBehavior, BoxLayout):
    icon = StringProperty("")
    text = StringProperty("")
    tint = ListProperty(BLUE)
    label_color = ListProperty(L_TXT)

    def on_state(self, *_):
        self.opacity = 0.55 if self.state == "down" else 1


class SheetItem(ButtonBehavior, BoxLayout):
    icon = StringProperty("")
    text = StringProperty("")
    tint = ListProperty(BLUE)
    danger = BooleanProperty(False)


class SelectRow(ToggleButtonBehavior, BoxLayout):
    text = StringProperty("")
    value = StringProperty("")


class LoadingRing(Widget):
    angle = NumericProperty(0)

    def __init__(self, **kw):
        super().__init__(**kw)
        self._ev = Clock.schedule_interval(self._spin, 1 / 60.0)

    def _spin(self, dt):
        self.angle = (self.angle + dt * 360) % 360


class Busy(ModalView):
    text = StringProperty("Procesando…")


class Toast(Label):
    pass


class Dialog(ModalView):
    title = StringProperty("")
    message = StringProperty("")

    def add_button(self, text, callback=None, primary=False, danger=False):
        if primary:
            btn = GradButton(text=text, height=dp(50), glow=False)
            if danger:
                btn.grad = App.get_running_app().grad_red
        else:
            btn = OutlineBtn(text=text, height=dp(50), fg=L_TXT)

        def press(*_):
            keep = callback() if callback else None
            if keep is not True:
                self.dismiss()
        btn.bind(on_release=press)
        self.ids.buttons.add_widget(btn)
        return btn


# =========================================================================== #
# Superposición de la cámara y recorte con lupa
# =========================================================================== #
def fitted_rect(widget, img_w, img_h, margin=0):
    W, H = widget.width - 2 * margin, widget.height - 2 * margin
    s = min(W / float(img_w), H / float(img_h)) if img_w and img_h else 1
    dw, dh = img_w * s, img_h * s
    return widget.x + (widget.width - dw) / 2.0, widget.y + (widget.height - dh) / 2.0, s


class CamOverlay(Widget):
    """Dibuja el contorno detectado sobre la vista previa."""
    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            self._fill_color = Color(0.1, 0.45, 1, 0.22)
            self._mesh = Mesh(mode="triangle_fan")
            self._line_color = Color(0.25, 0.6, 1, 1)
            self._line = Line(close=True, width=dp(2.2))

    def show(self, quad, frame_w, frame_h, stable=False):
        if quad is None:
            self._mesh.vertices = []
            self._mesh.indices = []
            self._line.points = []
            return
        x0, y0, s = fitted_rect(self, frame_w, frame_h)
        pts = [(x0 + x * s, y0 + (frame_h - y) * s) for x, y in quad]
        self._line.points = [c for p in pts for c in p]
        self._mesh.vertices = [v for x, y in pts for v in (x, y, 0, 0)]
        self._mesh.indices = [0, 1, 2, 3]
        if stable:
            self._line_color.rgba = (0.2, 0.85, 0.45, 1)
            self._fill_color.rgba = (0.2, 0.85, 0.45, 0.22)
        else:
            self._line_color.rgba = (0.25, 0.6, 1, 1)
            self._fill_color.rgba = (0.1, 0.45, 1, 0.22)


class CropWidget(Widget):
    """Imagen con 4 esquinas arrastrables y lupa de precisión."""
    valid = BooleanProperty(True)
    LOUPE = dp(118)
    ZOOM = 2.6

    def __init__(self, **kw):
        super().__init__(**kw)
        self.img = None
        self.tex = None
        self.quad = None
        self.drag = None
        self.bind(pos=self.redraw, size=self.redraw)

    def set_image(self, img, quad):
        self.img = img
        self.tex = np_to_texture(img)
        self.quad = np.array(quad, dtype=np.float64)
        self.redraw()

    def _rect(self):
        h, w = self.img.shape[:2]
        return fitted_rect(self, w, h, margin=dp(22))

    def to_widget(self, p):
        x0, y0, s = self._rect()
        return x0 + p[0] * s, y0 + (self.img.shape[0] - p[1]) * s

    def to_img(self, wx, wy):
        x0, y0, s = self._rect()
        h, w = self.img.shape[:2]
        return (min(max((wx - x0) / s, 0), w - 1), min(max(h - (wy - y0) / s, 0), h - 1))

    def redraw(self, *_):
        self.canvas.clear()
        if self.img is None:
            return
        h, w = self.img.shape[:2]
        x0, y0, s = self._rect()
        pts = [self.to_widget(p) for p in self.quad]
        self.valid = scanner.is_valid_quad(self.quad.astype(np.float32), min_area=w * h * 0.02)
        accent = (0.25, 0.6, 1, 1) if self.valid else (0.95, 0.3, 0.3, 1)
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=self.tex, pos=(x0, y0), size=(w * s, h * s))
            Color(accent[0], accent[1], accent[2], 0.18)
            Mesh(vertices=[v for x, y in pts for v in (x, y, 0, 0)], indices=[0, 1, 2, 3],
                 mode="triangle_fan")
            Color(*accent)
            Line(points=[c for p in pts for c in p], close=True, width=dp(2))
            for i, (px, py) in enumerate(pts):
                r = dp(13) if i == self.drag else dp(11)
                Color(1, 1, 1, 1)
                Ellipse(pos=(px - r, py - r), size=(2 * r, 2 * r))
                Color(*accent)
                Line(circle=(px, py, r), width=dp(2.4))
            if self.drag is not None:
                self._draw_loupe(pts[self.drag], self.quad[self.drag], s)

    def _draw_loupe(self, wpt, ipt, s):
        L = self.LOUPE
        h, w = self.img.shape[:2]
        half = L / (2.0 * s * self.ZOOM)            # mitad del área en píxeles de imagen
        cx, cy = ipt[0], h - ipt[1]                 # coordenadas de textura (origen abajo)
        rx0, ry0 = max(0, cx - half), max(0, cy - half)
        rx1, ry1 = min(w, cx + half), min(h, cy + half)
        if rx1 - rx0 < 2 or ry1 - ry0 < 2:
            return
        # Lupa en la esquina contraria al dedo
        lx = self.x + dp(14) if wpt[0] > self.center_x else self.right - L - dp(14)
        ly = self.top - L - dp(14)
        sc = L / (2 * half)
        ox, oy = lx + (rx0 - (cx - half)) * sc, ly + (ry0 - (cy - half)) * sc
        Color(0, 0, 0, 1)
        Rectangle(pos=(lx - dp(3), ly - dp(3)), size=(L + dp(6), L + dp(6)))
        Color(1, 1, 1, 1)
        Rectangle(texture=self.tex.get_region(rx0, ry0, rx1 - rx0, ry1 - ry0),
                  pos=(ox, oy), size=((rx1 - rx0) * sc, (ry1 - ry0) * sc))
        Color(0.25, 0.6, 1, 1)
        Line(rectangle=(lx, ly, L, L), width=dp(1.5))
        mx, my = lx + L / 2.0, ly + L / 2.0
        Line(points=[mx - dp(14), my, mx + dp(14), my], width=dp(1.2))
        Line(points=[mx, my - dp(14), mx, my + dp(14)], width=dp(1.2))

    def on_touch_down(self, touch):
        if self.img is None or not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        pts = [self.to_widget(p) for p in self.quad]
        d = [np.hypot(touch.x - x, touch.y - y) for x, y in pts]
        i = int(np.argmin(d))
        if d[i] < dp(48):
            self.drag = i
            # Arrastre relativo: el punto no "salta" bajo el dedo
            self._offset = (pts[i][0] - touch.x, pts[i][1] - touch.y)
            touch.grab(self)
            self.redraw()
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self and self.drag is not None:
            self.quad[self.drag] = self.to_img(touch.x + self._offset[0],
                                               touch.y + self._offset[1])
            self.redraw()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self.drag = None
            self.redraw()
            return True
        return super().on_touch_up(touch)


# =========================================================================== #
# Pantallas
# =========================================================================== #
class WelcomeScreen(Screen):
    pass


class AuthScreen(Screen):
    mode = StringProperty("login")
    error = StringProperty("")

    def on_pre_enter(self, *_):
        self.error = ""
        for k in ("pw", "pw2"):
            self.ids[k].text = ""

    def submit(self):
        app = App.get_running_app()
        ids = self.ids
        try:
            if self.mode == "register":
                if ids.pw.text != ids.pw2.text:
                    raise ValueError("Las contraseñas no coinciden")
                uid = app.store.create_user(ids.name.text, ids.email.text, ids.pw.text)
                user = app.store.get_user(uid)
            else:
                user = app.store.authenticate(ids.email.text, ids.pw.text)
                if not user:
                    raise ValueError("Correo o contraseña incorrectos")
        except ValueError as e:
            self.error = str(e)
            return
        app.login(user)


def _doc_row(app, doc):
    thumb = app.store.thumb_path(doc)
    row = DocRow(title=doc["title"], doc_id=doc["id"],
                 subtitle="%s  ·  %d %s" % (fmt_date(doc["updated"]), doc["pages"],
                                           "página" if doc["pages"] == 1 else "páginas"),
                 thumb=thumb if os.path.exists(thumb) else "")
    row.bind(on_release=lambda r: app.open_doc(r.doc_id),
             on_more=lambda r: app.doc_options(r.doc_id))
    return row


class HomeScreen(Screen):
    searching = BooleanProperty(False)
    has_folders = BooleanProperty(False)

    def on_pre_enter(self, *_):
        self.refresh()

    def refresh(self, *_):
        app = App.get_running_app()
        if not app.user:
            return
        q = self.ids.search.text.strip()
        self.searching = bool(q)
        uid = app.user["id"]

        box = self.ids.folders
        box.clear_widgets()
        folders = app.store.list_folders(uid)
        self.has_folders = bool(folders)
        if folders:
            for f in folders:
                card = FolderCard(title=f["name"], subtitle="%d documentos" % f["count"])
                card.bind(on_release=lambda w, fid=f["id"]: app.open_folder(fid))
                box.add_widget(card)
        else:
            box.add_widget(Label(text="Aún no tienes carpetas.", color=L_MUT,
                                 font_size=sp(13), size_hint_x=None, width=dp(240),
                                 halign="left", text_size=(dp(240), None)))

        lst = self.ids.recents
        lst.clear_widgets()
        docs = app.store.list_documents(uid, q, limit=None if q else 8)
        for d in docs:
            lst.add_widget(_doc_row(app, d))
        if not docs:
            lst.add_widget(EmptyState(
                icon=IC["search"] if q else IC["scan"],
                title="Sin resultados" if q else "Todavía no hay documentos",
                desc=("Prueba con otra palabra." if q else
                      "Pulsa Escanear para digitalizar el primero.")))


class DocsScreen(Screen):
    folder_id = ObjectProperty(None, allownone=True)
    folder_info = StringProperty("")

    def on_pre_enter(self, *_):
        self.refresh()

    def select_folder(self, fid):
        self.folder_id = fid
        self.refresh()

    def refresh(self, *_):
        app = App.get_running_app()
        if not app.user:
            return
        uid = app.user["id"]
        folders = app.store.list_folders(uid)
        if self.folder_id and not any(f["id"] == self.folder_id for f in folders):
            self.folder_id = None
        chips = self.ids.chips
        chips.clear_widgets()
        for fid, name in [(None, "Todos")] + [(f["id"], f["name"]) for f in folders]:
            c = Chip(text=name, group="folders", allow_no_selection=False,
                     state="down" if fid == self.folder_id else "normal")
            c.bind(on_release=lambda w, fid=fid: self.select_folder(fid))
            chips.add_widget(c)

        docs = app.store.list_documents(uid, self.ids.search.text, folder_id=self.folder_id)
        if self.folder_id:
            self.folder_info = "%d documentos en esta carpeta" % len(docs)
        lst = self.ids.list
        lst.clear_widgets()
        for d in docs:
            lst.add_widget(_doc_row(app, d))
        if not docs:
            lst.add_widget(EmptyState(icon=IC["folder_open"], title="Nada por aquí",
                                      desc="Los documentos que escanees aparecerán aquí."))


class ToolsScreen(Screen):
    pass


class ProfileScreen(Screen):
    n_docs = StringProperty("0")
    n_folders = StringProperty("0")
    usage = StringProperty("0 B")

    def on_pre_enter(self, *_):
        app = App.get_running_app()
        if not app.user:
            return
        uid = app.user["id"]
        self.n_docs = str(len(app.store.list_documents(uid)))
        self.n_folders = str(len(app.store.list_folders(uid)))
        self.usage = fmt_size(app.store.usage_bytes(uid))


class CameraScreen(Screen):
    n_pages = NumericProperty(0)
    hint = StringProperty("")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cam = None
        self._ev = None
        self.frame = None
        self.quad = None
        self.prev_quad = None
        self.stable = 0
        self._detecting = False
        self._tick_n = 0
        self._capturing = False
        self._rotation = 0

    def on_pre_enter(self, *_):
        self.n_pages = len(App.get_running_app().pages)
        self.hint = "Iniciando cámara…"
        self._capturing = False
        pu.request_camera(self._on_permission)

    @mainthread
    def _on_permission(self, granted):
        if self.manager.current != self.name:
            return
        if not granted:
            self.hint = "Permiso de cámara denegado. Usa Galería."
            return
        self.start_camera()

    def start_camera(self):
        try:
            from kivy.core.camera import Camera as CoreCamera
            if CoreCamera is None:
                raise RuntimeError
            for res in ((1920, 1080), (1280, 720), (640, 480)):
                try:
                    self.cam = CoreCamera(index=0, resolution=res, stopped=True)
                    self.cam.start()
                    break
                except Exception:
                    self.cam = None
            if self.cam is None:
                raise RuntimeError
        except Exception:
            self.hint = "Cámara no disponible. Usa Galería."
            return
        self._rotation = pu.camera_orientation(0)
        self.stable = 0
        self.quad = self.prev_quad = None
        self.hint = "Buscando documento…"
        self._ev = Clock.schedule_interval(self._tick, 1 / 24.0)

    def on_leave(self, *_):
        if self._ev:
            self._ev.cancel()
            self._ev = None
        if self.cam:
            try:
                self.cam.stop()
                if hasattr(self.cam, "_release_camera"):
                    self.cam._release_camera()
            except Exception:
                pass
            self.cam = None
        self.ids.overlay.show(None, 1, 1)

    def _grab(self):
        cam = self.cam
        if cam is None:
            return None
        if pu.IS_ANDROID and hasattr(cam, "grab_frame"):
            buf = cam.grab_frame()
            if buf is None:
                return None
            w, h = cam._resolution if hasattr(cam, "_resolution") else cam.resolution
            arr = np.frombuffer(bytes(buf), np.uint8)
            if arr.size < w * h * 3 // 2:
                return None
            frame = cv2.cvtColor(arr[: w * h * 3 // 2].reshape(h * 3 // 2, w),
                                 cv2.COLOR_YUV2BGR_NV21)
        else:
            tex = cam.texture
            if tex is None:
                return None
            arr = np.frombuffer(tex.pixels, np.uint8).reshape(tex.height, tex.width, 4)
            frame = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
               270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(self._rotation)
        return cv2.rotate(frame, rot) if rot is not None else frame

    def _tick(self, dt):
        if self._capturing:
            return
        frame = self._grab()
        if frame is None:
            return
        self.frame = frame
        disp, s = scanner.resize_max(frame, 960)
        self.ids.preview.texture = np_to_texture(disp)
        self._tick_n += 1
        if self._tick_n % 3 == 0 and not self._detecting:
            self._detecting = True
            small = disp.copy()
            threading.Thread(target=self._detect, args=(small,), daemon=True).start()
        self.ids.overlay.show(self.quad, disp.shape[1], disp.shape[0], self.stable >= 2)

    def _detect(self, small):
        try:
            quad, conf = scanner.detect_document(small, refine=False)
        except Exception:
            quad, conf = None, 0
        self._on_detect(quad if conf > 0.45 else None, small.shape)

    @mainthread
    def _on_detect(self, quad, shape):
        self._detecting = False
        if self._capturing or self.cam is None:
            return
        h, w = shape[:2]
        if quad is None:
            self.quad = None
            self.stable = 0
            self.hint = "Buscando documento…"
            return
        if self.prev_quad is not None and \
                np.max(np.linalg.norm(quad - self.prev_quad, axis=1)) < 0.025 * max(w, h):
            self.stable += 1
        else:
            self.stable = 0
        self.prev_quad = self.quad = quad
        auto = self.ids.auto.state == "down"
        if auto and self.stable >= 4:
            self.capture()
        else:
            self.hint = "Mantén el teléfono firme…" if auto else "Documento detectado"

    def capture(self):
        if self.frame is None or self._capturing:
            if self.frame is None:
                App.get_running_app().toast("La cámara no está lista")
            return
        self._capturing = True
        self.hint = ""
        frame = self.frame.copy()
        # Flash visual
        flash = Widget()
        with flash.canvas:
            Color(1, 1, 1, 0.85)
            Rectangle(pos=self.pos, size=self.size)
        self.add_widget(flash)
        Animation(opacity=0, d=0.35).start(flash)
        Clock.schedule_once(lambda dt: self.remove_widget(flash), 0.4)
        App.get_running_app().process_image(frame, ask_crop=True)


class CropScreen(Screen):
    valid = BooleanProperty(True)

    def on_kv_post(self, *_):
        self.ids.crop.bind(valid=self.setter("valid"))

    def load(self, img, quad):
        self.img = img
        self.ids.crop.set_image(img, quad)

    def rotate(self):
        crop = self.ids.crop
        h = crop.img.shape[0]
        q = crop.quad
        new_quad = np.stack([h - 1 - q[:, 1], q[:, 0]], axis=1)  # rotación horaria
        self.img = scanner.rotate90(crop.img)
        crop.set_image(self.img, scanner.order_points(new_quad))

    def auto(self):
        img = self.ids.crop.img
        quad, conf = scanner.detect_document(img)
        if conf == 0:
            App.get_running_app().toast("No se encontraron bordes claros")
        self.ids.crop.set_image(img, quad)

    def full(self):
        self.ids.crop.set_image(self.ids.crop.img, scanner.full_quad(self.ids.crop.img))

    def confirm(self):
        crop = self.ids.crop
        img, quad = crop.img, crop.quad.astype(np.float32)
        app = App.get_running_app()
        run_bg(lambda: scanner.warp(img, quad),
               lambda warped: app.open_enhance(warped, "magic"), busy="Enderezando…")


class EnhanceScreen(Screen):
    working = BooleanProperty(False)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.warped = None
        self.mode = "magic"
        self._preview = None
        self._job = 0

    def on_kv_post(self, *_):
        for key, name in scanner.FILTERS:
            c = Chip(text=name, group="filter", allow_no_selection=False,
                     fg=(1, 1, 1, 1), bg=(1, 1, 1, 0.1), line=(1, 1, 1, 0.1))
            c.mode = key
            c.bind(on_release=lambda w: self.set_mode(w.mode))
            self.ids.chips.add_widget(c)

    def load(self, warped, mode="magic"):
        self.warped = warped
        self._preview = scanner.resize_max(warped, 1300)[0]
        self.set_mode(mode)

    def set_mode(self, mode):
        self.mode = mode
        for c in self.ids.chips.children:
            c.state = "down" if c.mode == mode else "normal"
        self._job += 1
        job = self._job
        self.working = True
        src = self._preview

        def work():
            return scanner.enhance(src, mode)

        def done(res):
            if job == self._job:
                self.ids.img.texture = np_to_texture(res)
                self.working = False
        run_bg(work, done)

    def rotate(self):
        self.warped = scanner.rotate90(self.warped)
        self._preview = scanner.resize_max(self.warped, 1300)[0]
        self.set_mode(self.mode)

    def confirm(self):
        warped, mode = self.warped, self.mode
        app = App.get_running_app()
        run_bg(lambda: scanner.enhance(warped, mode),
               lambda final: app.add_page(warped, final, mode), busy="Aplicando filtro…")


class PagesScreen(Screen):
    folder_name = StringProperty("Ninguna")
    folder_id = ObjectProperty(None, allownone=True)

    def on_pre_enter(self, *_):
        app = App.get_running_app()
        if not self.ids.title.text:
            self.ids.title.text = "Escaneo " + datetime.datetime.now().strftime("%d-%m-%Y %H.%M")
        self.ids.ocr.state = "down" if ocr.available() else "normal"
        self.refresh()

    def reset(self):
        self.ids.title.text = ""
        self.folder_id = None
        self.folder_name = "Ninguna"

    def refresh(self):
        app = App.get_running_app()
        grid = self.ids.grid
        grid.clear_widgets()
        for i, p in enumerate(app.pages):
            if "tex" not in p:
                p["tex"] = np_to_texture(scanner.resize_max(p["final"], 420)[0])
            grid.add_widget(PageThumb(texture_img=p["tex"], number=i + 1))

    def choose_folder(self):
        app = App.get_running_app()
        folders = app.store.list_folders(app.user["id"])
        items = [(IC["close"], "Ninguna", lambda: self._set_folder(None, "Ninguna"))]
        items += [(IC["folder"], f["name"], (lambda f=f: self._set_folder(f["id"], f["name"])))
                  for f in folders]
        items.append((IC["new_folder"], "Nueva carpeta…",
                      lambda: app.new_folder(on_created=self._set_folder)))
        app.sheet("Guardar en carpeta", items)

    def _set_folder(self, fid, name):
        self.folder_id = fid
        self.folder_name = name


class DocScreen(Screen):
    doc_id = StringProperty("")
    title = StringProperty("")
    info = StringProperty("")
    page_label = StringProperty("")

    def load(self, doc_id):
        app = App.get_running_app()
        doc = app.store.get_document(doc_id)
        if not doc:
            return False
        self.doc_id = doc_id
        self.title = doc["title"]
        pdf = app.store.pdf_path(doc)
        size = fmt_size(os.path.getsize(pdf)) if os.path.exists(pdf) else "-"
        has_text = any(t.strip() for t in app.store.texts_of(doc))
        self.info = "%d %s  ·  PDF %s%s" % (doc["pages"], "página" if doc["pages"] == 1 else "páginas",
                                           size, "  ·  con texto buscable" if has_text else "")
        car = self.ids.car
        car.clear_widgets()
        for p in app.store.page_paths(doc):
            car.add_widget(Image(source=p, fit_mode="contain", nocache=True))
        car.index = 0
        self.page_label = "1 / %d" % len(car.slides)
        return True


class TextScreen(Screen):
    doc_id = StringProperty("")
    empty_hint = StringProperty("")

    def load(self, doc_id=None, text=""):
        app = App.get_running_app()
        self.doc_id = doc_id or ""
        if doc_id:
            doc = app.store.get_document(doc_id)
            text = app.store.join_texts(app.store.texts_of(doc))
        self.ids.txt.text = text
        if ocr.available():
            self.empty_hint = "No se encontró texto. Pulsa ✦ arriba para reconocerlo."
        else:
            self.empty_hint = ("El OCR no está disponible en este equipo. "
                               "En Android funciona sin internet.")

    def copy(self):
        Clipboard.copy(self.ids.txt.text)
        App.get_running_app().toast("Texto copiado")

    def share(self):
        if pu.IS_ANDROID:
            pu.share_text(self.ids.txt.text)
        else:
            self.copy()

    def save(self):
        app = App.get_running_app()
        doc = app.store.get_document(self.doc_id)
        texts = app.store.split_texts(self.ids.txt.text, doc["pages"])
        run_bg(lambda: app.rebuild_doc(doc, texts), lambda _: app.toast("Texto guardado"),
               busy="Guardando…")

    def run_ocr(self):
        app = App.get_running_app()
        doc = app.store.get_document(self.doc_id)
        paths = app.store.page_paths(doc)

        def work():
            texts = [ocr.recognize(p) for p in paths]
            app.rebuild_doc(doc, texts)
            return texts

        run_bg(work, lambda texts: self.load(self.doc_id), busy="Reconociendo texto…")


# =========================================================================== #
# Aplicación
# =========================================================================== #
BACK_MAP = {"register": "welcome", "login": "welcome", "docs": "home", "tools": "home",
            "profile": "home", "crop": "camera", "enhance": "crop", "pages": "camera"}


class ScanGaiaApp(App):
    title = "ScanGaia"
    user = ObjectProperty(None, allownone=True)
    grad = ObjectProperty(None)
    pages = ListProperty([])
    edit_index = ObjectProperty(None, allownone=True)
    append_to = StringProperty("")

    # ----------------------------------------------------------- arranque
    def build(self):
        os.chdir(APP_DIR)
        Window.clearcolor = hexc("#070B12")
        Window.softinput_mode = "below_target"
        self.grad = make_gradient()
        self.grad_red = make_gradient("#F0565B", "#D3313A")
        self.store = Store(os.path.join(self.user_data_dir, "scangaia"))
        self._busy = None
        self._prev_screen = None
        Builder.load_file(os.path.join(APP_DIR, "ui.kv"))

        sm = ScreenManager(transition=SlideTransition(duration=0.22))
        sm.add_widget(WelcomeScreen(name="welcome"))
        sm.add_widget(AuthScreen(name="register", mode="register"))
        sm.add_widget(AuthScreen(name="login", mode="login"))
        for cls, name in ((HomeScreen, "home"), (DocsScreen, "docs"), (ToolsScreen, "tools"),
                          (ProfileScreen, "profile"), (CameraScreen, "camera"),
                          (CropScreen, "crop"), (EnhanceScreen, "enhance"),
                          (PagesScreen, "pages"), (DocScreen, "doc"), (TextScreen, "text")):
            sm.add_widget(cls(name=name))
        self.sm = sm

        uid = self.store.load_session()
        if uid:
            self.user = self.store.get_user(uid)
            sm.current = "home"
        Window.bind(on_keyboard=self._on_key)
        return sm

    def on_pause(self):
        return True

    def on_resume(self):
        if self.sm.current == "camera":
            cam = self.sm.get_screen("camera")
            cam.on_leave()
            cam.on_pre_enter()

    # --------------------------------------------------------- navegación
    def go(self, name, direction="left"):
        if self.sm.current == name:
            return
        self._prev_screen = self.sm.current
        self.sm.transition = SlideTransition(duration=0.22, direction=direction)
        self.sm.current = name

    def go_tab(self, name):
        self.sm.transition = NoTransition()
        self.sm.current = name

    def back(self):
        cur = self.sm.current
        if cur == "camera":
            if self.pages:
                return self.go("pages", "right")
            return self.go(self._scan_origin(), "right")
        if cur == "pages":
            return self.go("camera", "right")
        if cur == "enhance" and self.edit_index is not None:
            self.edit_index = None
            return self.go("pages", "right")
        if cur == "crop" and getattr(self, "_crop_origin", None):
            return self.go(self._crop_origin, "right")
        if cur == "doc":
            return self.go(getattr(self, "_doc_origin", "home") or "home", "right")
        if cur == "text":
            return self.go("doc" if self.sm.get_screen("text").doc_id else "home", "right")
        if cur in BACK_MAP:
            if cur in ("docs", "tools", "profile"):
                return self.go_tab("home")
            return self.go(BACK_MAP[cur], "right")
        return False

    def _scan_origin(self):
        return "doc" if self.append_to else "home"

    def _on_key(self, window, key, *args):
        if key == 27:   # botón Atrás de Android / Esc
            if self._busy:
                return True
            for w in list(Window.children):
                if isinstance(w, ModalView):
                    w.dismiss()
                    return True
            if self.sm.current in ("home", "welcome"):
                return False   # sale de la app
            self.back()
            return True
        return False

    # ------------------------------------------------------------- avisos
    def toast(self, text, duration=2.4):
        t = Toast(text=text)
        Window.add_widget(t)
        t.texture_update()
        t.pos = (Window.width / 2.0 - t.width / 2.0, dp(100))
        t.opacity = 0
        Animation(opacity=1, d=0.2).start(t)

        def hide(dt):
            a = Animation(opacity=0, d=0.3)
            a.bind(on_complete=lambda *x: Window.remove_widget(t))
            a.start(t)
        Clock.schedule_once(hide, duration)

    def show_busy(self, text):
        if self._busy:
            self._busy.text = text
            return
        self._busy = Busy(text=text)
        self._busy.open(animation=False)

    def hide_busy(self):
        if self._busy:
            self._busy.dismiss(animation=False)
            self._busy = None

    def dialog(self, title, message="", fields=(), buttons=()):
        d = Dialog(title=title, message=message)
        inputs = []
        for hint, value, password in fields:
            f = Field(hint_text=hint, text=value, password=password)
            d.ids.content.add_widget(f)
            inputs.append(f)
        for b in buttons:
            d.add_button(*b)
        d.open()
        if inputs:
            Clock.schedule_once(lambda dt: setattr(inputs[0], "focus", True), 0.25)
        return d, inputs

    def confirm(self, title, message, on_yes, yes_text="Eliminar", danger=True):
        self.dialog(title, message, buttons=[("Cancelar", None),
                                             (yes_text, on_yes, True, danger)])

    def ask_text(self, title, hint, value, on_ok, ok_text="Guardar"):
        holder = {}

        def ok():
            text = holder["inputs"][0].text.strip()
            if not text:
                return True
            on_ok(text)
        _, inputs = self.dialog(title, fields=[(hint, value, False)],
                                buttons=[("Cancelar", None), (ok_text, ok, True)])
        holder["inputs"] = inputs

    def sheet(self, title, items):
        """Menú de opciones. items: (icono, texto, callback[, peligro])"""
        d = Dialog(title=title)
        for it in items:
            icon, text, cb = it[:3]
            danger = len(it) > 3 and it[3]
            row = SheetItem(icon=icon, text=text, danger=danger, tint=RED if danger else BLUE)

            def press(w, cb=cb):
                d.dismiss()
                Clock.schedule_once(lambda dt: cb(), 0.05)
            row.bind(on_release=press)
            d.ids.content.add_widget(row)
        d.ids.buttons.height = 0
        d.ids.buttons.opacity = 0
        d.open()

    def select_docs(self, title, on_done, multiple=True, ok_text="Continuar"):
        docs = self.store.list_documents(self.user["id"])
        if not docs:
            self.toast("Primero escanea o importa un documento")
            return
        d = Dialog(title=title)
        sv = ScrollView(size_hint_y=None, height=min(dp(56) * len(docs) + dp(4), Window.height * 0.5))
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        rows = []
        for doc in docs:
            r = SelectRow(text="%s  (%d p.)" % (doc["title"], doc["pages"]), value=doc["id"],
                          group=None if multiple else "sel")
            rows.append(r)
            box.add_widget(r)
        sv.add_widget(box)
        d.ids.content.add_widget(sv)

        def ok():
            chosen = [r.value for r in rows if r.state == "down"]
            if not chosen:
                self.toast("Selecciona al menos un documento")
                return True
            on_done(chosen)
        d.add_button("Cancelar")
        d.add_button(ok_text, ok, primary=True)
        d.open()

    # ------------------------------------------------------------ cuentas
    def login(self, user):
        self.user = user
        self.store.save_session(user["id"])
        self.go_tab("home")

    def logout(self):
        def do():
            self.store.clear_session()
            self.user = None
            self.go("welcome", "right")
        self.confirm("Cerrar sesión", "Tus documentos seguirán guardados en este teléfono.",
                     do, "Cerrar sesión", danger=False)

    def change_password(self):
        holder = {}

        def ok():
            old, new, new2 = [f.text for f in holder["f"]]
            if new != new2:
                self.toast("Las contraseñas nuevas no coinciden")
                return True
            try:
                self.store.change_password(self.user["id"], old, new)
            except ValueError as e:
                self.toast(str(e))
                return True
            self.toast("Contraseña actualizada")
        _, f = self.dialog("Cambiar contraseña",
                           fields=[("Contraseña actual", "", True), ("Nueva contraseña", "", True),
                                   ("Repite la nueva", "", True)],
                           buttons=[("Cancelar", None), ("Guardar", ok, True)])
        holder["f"] = f

    def delete_account(self):
        def do():
            self.store.delete_user(self.user["id"])
            self.store.clear_session()
            self.user = None
            self.go("welcome", "right")
            self.toast("Cuenta eliminada")
        self.confirm("Eliminar cuenta",
                     "Se borrarán tu cuenta y TODOS tus documentos. No se puede deshacer.",
                     do, "Eliminar todo")

    # ---------------------------------------------------------- escaneo
    def start_scan(self, append_to=""):
        self.pages = []
        self.edit_index = None
        self.append_to = append_to
        self.sm.get_screen("pages").reset()
        self.go("camera")

    def capture_hd(self):
        pu.capture_hd(self._on_hd)

    @mainthread
    def _on_hd(self, path):
        if path:
            self._load_and_process([path])

    def process_image(self, img, ask_crop=True, origin="camera"):
        """Detecta bordes (en segundo plano) y abre el recorte."""
        self._crop_origin = origin

        def work():
            return img, scanner.detect_document(img)

        def done(res):
            image, (quad, conf) = res
            self.sm.get_screen("crop").load(image, quad)
            self.go("crop")
        run_bg(work, done, busy="Detectando bordes…")

    def open_enhance(self, warped, mode):
        self.sm.get_screen("enhance").load(warped, mode)
        self.go("enhance")

    def add_page(self, warped, final, mode):
        page = {"warped": warped, "final": final, "mode": mode}
        pages = list(self.pages)
        if self.edit_index is not None and self.edit_index < len(pages):
            pages[self.edit_index] = page
            self.edit_index = None
            self.pages = pages
            self.go("pages", "right")
        else:
            pages.append(page)
            self.pages = pages
            self.toast("Página %d añadida" % len(pages), 1.4)
            self.go("camera" if self._crop_origin == "camera" else "pages", "right")

    def edit_page(self, index):
        p = self.pages[index]
        self.edit_index = index
        self.open_enhance(p["warped"], p["mode"])

    def delete_page(self, index):
        def do():
            pages = list(self.pages)
            pages.pop(index)
            self.pages = pages
            self.sm.get_screen("pages").refresh()
        self.confirm("Eliminar página", "¿Quitar la página %d?" % (index + 1), do, "Quitar")

    def move_page(self, index):
        if index <= 0:
            return
        pages = list(self.pages)
        pages[index - 1], pages[index] = pages[index], pages[index - 1]
        self.pages = pages
        self.sm.get_screen("pages").refresh()

    # ----------------------------------------------------------- importar
    def _pick(self, mime, multiple, callback, exts):
        if pu.IS_ANDROID:
            @mainthread
            def got(paths):
                if not paths and pu.last_error:
                    self.toast(pu.last_error, 4)
                callback(paths)

            @mainthread
            def start(_granted):
                try:
                    pu.pick_files(mime, multiple, got)
                except Exception as e:
                    traceback.print_exc()
                    self.toast("No se pudo abrir el selector: %s" % e, 4)
            pu.request_storage(start)
            return
        # Escritorio: selector de archivos propio
        from kivy.uix.filechooser import FileChooserListView
        d = Dialog(title="Seleccionar archivo")
        fc = FileChooserListView(path=os.path.expanduser("~"), multiselect=multiple,
                                 filters=["*" + e for e in exts] + ["*" + e.upper() for e in exts],
                                 size_hint_y=None, height=Window.height * 0.55)
        d.ids.content.add_widget(fc)
        d.add_button("Cancelar")
        d.add_button("Abrir", lambda: callback(list(fc.selection)), primary=True)
        d.open()

    def import_images(self, keep_session=False):
        if not keep_session:
            self.pages = []
            self.append_to = ""
            self.edit_index = None
            self.sm.get_screen("pages").reset()
        self._pick("image/*", True, self._load_and_process, (".jpg", ".jpeg", ".png", ".webp", ".bmp"))

    def _load_and_process(self, paths):
        if not paths:
            return
        origin = "camera" if self.sm.current == "camera" else "pages"
        if len(paths) == 1:
            try:
                img = scanner.load_image(paths[0])
            except ValueError as e:
                return self.toast(str(e))
            return self.process_image(img, origin=origin)

        def work():   # Lote: recorte y filtro automáticos
            out = []
            for p in paths:
                img = scanner.load_image(p)
                warped, final = scanner.auto_process(img)
                out.append({"warped": warped, "final": final, "mode": "magic"})
            return out

        def done(new_pages):
            self.pages = list(self.pages) + new_pages
            self._crop_origin = "pages"
            self.go("pages")
            self.toast("%d páginas importadas. Toca ✎ para ajustar alguna." % len(new_pages))
        run_bg(work, done, busy="Procesando %d imágenes…" % len(paths))

    def import_pdf(self):
        def got(paths):
            if not paths:
                return
            src = paths[0]
            title = os.path.splitext(os.path.basename(src))[0]
            if title.startswith("in_"):
                title = "PDF importado " + datetime.datetime.now().strftime("%d-%m-%Y")

            def work():
                uid = self.user["id"]
                doc_id, folder = self.store.new_document_folder(uid)
                try:
                    imgs = pu.render_pdf(src, os.path.join(folder, "_tmp"))
                    if not imgs:
                        raise ValueError("El PDF no tiene páginas")
                    for i, p in enumerate(imgs):
                        os.replace(p, os.path.join(folder, "page_%03d.jpg" % (i + 1)))
                    shutil.rmtree(os.path.join(folder, "_tmp"), ignore_errors=True)
                    shutil.copyfile(src, os.path.join(folder, "documento.pdf"))
                    self._make_thumb(os.path.join(folder, "page_001.jpg"),
                                     os.path.join(folder, "thumb.jpg"))
                    self.store.add_document(uid, doc_id, title, len(imgs), [""] * len(imgs))
                except Exception:
                    shutil.rmtree(folder, ignore_errors=True)
                    raise
                return doc_id
            run_bg(work, lambda doc_id: self.open_doc(doc_id, origin="home"),
                   busy="Importando PDF…", error_msg="No se pudo abrir el PDF")
        self._pick("application/pdf", False, got, (".pdf",))

    # ------------------------------------------------------------ guardar
    @staticmethod
    def _make_thumb(src, dst):
        img = scanner.load_image(src)
        scanner.save_jpeg(img, dst, quality=80, max_side=260)

    def rebuild_doc(self, doc, texts=None):
        """Regenera el PDF (y la miniatura) de un documento a partir de sus páginas."""
        texts = texts if texts is not None else self.store.texts_of(doc)
        paths = self.store.page_paths(doc)
        pdfgen.build_pdf(paths, self.store.pdf_path(doc), texts=texts, title=doc["title"])
        self._make_thumb(paths[0], self.store.thumb_path(doc))
        self.store.update_document(doc["id"], texts=texts, pages=len(paths))

    def save_document(self):
        screen = self.sm.get_screen("pages")
        title = screen.ids.title.text.strip() or "Documento"
        do_ocr = screen.ids.ocr.state == "down"
        pages = [p["final"] for p in self.pages]
        uid = self.user["id"]
        append_to = self.append_to
        folder_id = screen.folder_id

        def work():
            if append_to:
                doc = self.store.get_document(append_to)
                folder = self.store.doc_dir(doc)
                start = doc["pages"]
                doc_id = append_to
            else:
                doc_id, folder = self.store.new_document_folder(uid)
                start = 0
            new_paths = []
            for i, img in enumerate(pages):
                p = os.path.join(folder, "page_%03d.jpg" % (start + i + 1))
                scanner.save_jpeg(img, p)
                new_paths.append(p)
            new_texts = []
            if do_ocr and ocr.available():
                for p in new_paths:
                    try:
                        new_texts.append(ocr.recognize(p))
                    except Exception:
                        traceback.print_exc()
                        new_texts.append("")
            else:
                new_texts = [""] * len(new_paths)
            if append_to:
                texts = self.store.texts_of(doc) + new_texts
                self.store.update_document(doc_id, pages=start + len(pages))
            else:
                texts = new_texts
                self.store.add_document(uid, doc_id, title, len(pages), texts, folder_id)
            self.rebuild_doc(self.store.get_document(doc_id), texts)
            return doc_id

        def done(doc_id):
            self.pages = []
            self.append_to = ""
            screen.reset()
            self.toast("Documento guardado")
            self.open_doc(doc_id, origin="home")

        busy = "Reconociendo texto y creando PDF…" if do_ocr and ocr.available() else "Creando PDF…"
        run_bg(work, done, busy=busy, error_msg="No se pudo guardar el documento")

    # ---------------------------------------------------------- documentos
    def open_doc(self, doc_id, origin=None):
        self._doc_origin = origin or (self.sm.current if self.sm.current in
                                      ("home", "docs") else "home")
        if self.sm.get_screen("doc").load(doc_id):
            self.go("doc")

    def open_text(self, doc_id):
        self.sm.get_screen("text").load(doc_id)
        self.go("text")

    def _export_name(self, doc):
        return "%s.pdf" % safe_filename(doc["title"])

    def share_doc(self, doc_id):
        doc = self.store.get_document(doc_id)
        try:
            pu.share_file(self.store.pdf_path(doc), self._export_name(doc))
        except Exception:
            traceback.print_exc()
            self.toast("No se pudo compartir")

    def download_doc(self, doc_id):
        doc = self.store.get_document(doc_id)
        run_bg(lambda: pu.save_to_downloads(self.store.pdf_path(doc), self._export_name(doc)),
               self.toast, error_msg="No se pudo guardar en Descargas")

    def append_pages(self, doc_id):
        self.start_scan(append_to=doc_id)

    def rename_doc(self, doc_id):
        doc = self.store.get_document(doc_id)

        def ok(text):
            self.store.update_document(doc_id, title=text)
            doc2 = self.store.get_document(doc_id)
            run_bg(lambda: self.rebuild_doc(doc2), lambda _: self._after_doc_change(doc_id))
        self.ask_text("Renombrar", "Nombre del documento", doc["title"], ok)

    def _after_doc_change(self, doc_id=None):
        cur = self.sm.current
        if cur == "doc" and doc_id:
            self.sm.get_screen("doc").load(doc_id)
        elif cur in ("home", "docs"):
            self.sm.get_screen(cur).refresh()

    def doc_options(self, doc_id):
        doc = self.store.get_document(doc_id)
        if not doc:
            return
        self.sheet(doc["title"], [
            (IC["share"], "Compartir PDF", lambda: self.share_doc(doc_id)),
            (IC["download"], "Guardar en Descargas", lambda: self.download_doc(doc_id)),
            (IC["subject"], "Ver texto", lambda: self.open_text(doc_id)),
            (IC["edit"], "Renombrar", lambda: self.rename_doc(doc_id)),
            (IC["drive_move"], "Mover a carpeta", lambda: self.move_doc(doc_id)),
            (IC["delete"], "Eliminar", lambda: self.delete_doc(doc_id), True),
        ])

    def delete_doc(self, doc_id):
        def do():
            self.store.delete_document(doc_id)
            if self.sm.current == "doc":
                self.back()
            else:
                self._after_doc_change()
            self.toast("Documento eliminado")
        self.confirm("Eliminar documento", "Esta acción no se puede deshacer.", do)

    def move_doc(self, doc_id):
        def to(fid):
            self.store.update_document(doc_id, folder_id=fid)
            self._after_doc_change(doc_id)
            self.toast("Documento movido")
        folders = self.store.list_folders(self.user["id"])
        items = [(IC["close"], "Sin carpeta", lambda: to(None))]
        items += [(IC["folder"], f["name"], (lambda f=f: to(f["id"]))) for f in folders]
        items.append((IC["new_folder"], "Nueva carpeta…",
                      lambda: self.new_folder(on_created=lambda fid, n: to(fid))))
        self.sheet("Mover a carpeta", items)

    # ------------------------------------------------------------ carpetas
    def new_folder(self, on_created=None):
        def ok(name):
            fid = self.store.create_folder(self.user["id"], name)
            if on_created:
                on_created(fid, name)
            self._after_doc_change()
        self.ask_text("Nueva carpeta", "Nombre de la carpeta", "", ok, "Crear")

    def open_folder(self, fid):
        self.go_tab("docs")
        self.sm.get_screen("docs").select_folder(fid)

    def rename_folder(self, fid):
        f = next((x for x in self.store.list_folders(self.user["id"]) if x["id"] == fid), None)
        if f:
            self.ask_text("Renombrar carpeta", "Nombre", f["name"],
                          lambda n: (self.store.rename_folder(fid, n), self._after_doc_change()))

    def delete_folder(self, fid):
        def do():
            self.store.delete_folder(fid)
            self.sm.get_screen("docs").select_folder(None)
        self.confirm("Eliminar carpeta", "Los documentos no se borran; quedarán sin carpeta.", do)

    # --------------------------------------------------------- herramientas
    def tool_ocr(self):
        if not ocr.available():
            self.toast("El OCR funciona en la app de Android (o instala Tesseract en PC)")
            return

        def got(paths):
            if not paths:
                return

            def work():
                img = scanner.load_image(paths[0])
                _, final = scanner.auto_process(img, "gray")
                tmp = os.path.join(pu.cache_dir(), "ocr_tmp.jpg")
                scanner.save_jpeg(final, tmp)
                return ocr.recognize(tmp)

            def done(text):
                self.sm.get_screen("text").load(None, text)
                self.go("text")
                if not text:
                    self.toast("No se encontró texto en la imagen")
            run_bg(work, done, busy="Reconociendo texto…")
        self._pick("image/*", False, got, (".jpg", ".jpeg", ".png", ".webp"))

    def tool_merge(self):
        def chosen(ids):
            if len(ids) < 2:
                self.toast("Elige al menos 2 documentos")
                return
            docs = [self.store.get_document(i) for i in ids]

            def work():
                uid = self.user["id"]
                doc_id, folder = self.store.new_document_folder(uid)
                texts, n = [], 0
                for d in docs:
                    t = self.store.texts_of(d)
                    for i, p in enumerate(self.store.page_paths(d)):
                        n += 1
                        shutil.copyfile(p, os.path.join(folder, "page_%03d.jpg" % n))
                        texts.append(t[i] if i < len(t) else "")
                title = "Unido - " + docs[0]["title"]
                self.store.add_document(uid, doc_id, title, n, texts)
                self.rebuild_doc(self.store.get_document(doc_id), texts)
                return doc_id
            run_bg(work, lambda did: self.open_doc(did, origin="home"), busy="Uniendo documentos…")
        self.select_docs("Unir documentos (en orden)", chosen, ok_text="Unir")

    def tool_compress(self):
        def chosen(ids):
            doc = self.store.get_document(ids[0])
            pdf = self.store.pdf_path(doc)

            def work():
                before = os.path.getsize(pdf)
                for p in self.store.page_paths(doc):
                    img = scanner.load_image(p)
                    scanner.save_jpeg(img, p, quality=62, max_side=1600)
                self.rebuild_doc(doc)
                return before, os.path.getsize(pdf)

            def done(r):
                self.toast("PDF comprimido: %s → %s" % (fmt_size(r[0]), fmt_size(r[1])), 3.2)
                self.open_doc(doc["id"], origin="home")
            run_bg(work, done, busy="Comprimiendo…")

        def ask(ids):
            self.confirm("Comprimir documento",
                         "Se reducirá la resolución de las páginas para que el PDF pese menos.",
                         lambda: chosen(ids), "Comprimir", danger=False)
        self.select_docs("Elige un documento", ask, multiple=False, ok_text="Continuar")


if __name__ == "__main__":
    ScanGaiaApp().run()
