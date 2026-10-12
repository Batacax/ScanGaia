"""
Recorrido automático de la interfaz que guarda capturas de cada pantalla.
Uso (escritorio):  xvfb-run -s "-screen 0 412x880x24" python3 tests/ui_tour.py SALIDA
"""
import os
import sys
import tempfile

os.environ.setdefault("KIVY_NO_ARGS", "1")
from kivy.config import Config
Config.set("graphics", "width", "412")
Config.set("graphics", "height", "880")
Config.set("graphics", "resizable", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cv2  # noqa
from kivy.clock import Clock  # noqa
from kivy.core.window import Window  # noqa

import main  # noqa
from core import scanner  # noqa
from tests.test_core import synthetic_photo  # noqa

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(OUT, exist_ok=True)
DATA = tempfile.mkdtemp()


class TourApp(main.ScanGaiaApp):
    @property
    def user_data_dir(self):
        return DATA

    def on_start(self):
        self.steps = [self.s_welcome, self.s_register, self.s_do_register, self.s_home_empty,
                      self.s_crop, self.s_drag, self.s_enhance, self.s_add, self.s_pages,
                      self.s_save, self.s_doc, self.s_home, self.s_docs, self.s_tools,
                      self.s_profile, self.s_sheet, self.s_text, self.s_end]
        self.i = 0
        Clock.schedule_once(self.next, 1.0)

    def shot(self, name):
        path = os.path.join(OUT, name + ".png")
        Window.screenshot(name=path)
        # Kivy añade un sufijo numérico: renombrar
        d = os.path.dirname(path)
        for f in sorted(os.listdir(d)):
            if f.startswith(name) and f != name + ".png" and f.endswith(".png"):
                os.replace(os.path.join(d, f), path)

    def next(self, *_):
        if self.i > 0:
            pass
        step = self.steps[self.i]
        self.i += 1
        delay = step() or 1.2
        if self.i < len(self.steps):
            Clock.schedule_once(self.next, delay)

    def s_welcome(self):
        self.shot("01_bienvenida")

    def s_register(self):
        self.go("register")

    def s_do_register(self):
        s = self.sm.get_screen("register")
        s.ids.name.text = "tonka1994gomez"
        s.ids.email.text = "demo@correo.com"
        s.ids.pw.text = s.ids.pw2.text = "secreto1"
        self.shot("02_registro")
        s.submit()

    def s_home_empty(self):
        self.shot("03_inicio_vacio")
        self.photo, _ = synthetic_photo(1)
        self.start_scan()
        return 0.8

    def s_crop(self):
        self.process_image(self.photo, origin="camera")
        return 2.0

    def s_drag(self):
        self.shot("04_recorte")
        # Simula arrastrar una esquina para mostrar la lupa
        crop = self.sm.get_screen("crop").ids.crop
        crop.drag = 1
        crop.redraw()

        def later(dt):
            self.shot("05_recorte_lupa")
            crop.drag = None
            crop.redraw()
        Clock.schedule_once(later, 0.3)
        return 0.8

    def s_enhance(self):
        self.sm.get_screen("crop").confirm()
        return 2.5

    def s_add(self):
        self.shot("06_mejorar")
        self.sm.get_screen("enhance").set_mode("bw")
        return 1.5

    def s_pages(self):
        self.shot("07_filtro_bn")
        self.sm.get_screen("enhance").set_mode("magic")
        Clock.schedule_once(lambda dt: self.sm.get_screen("enhance").confirm(), 1.0)
        Clock.schedule_once(lambda dt: self.go("pages"), 2.5)
        return 3.5

    def s_save(self):
        self.shot("08_paginas")
        self.save_document()
        return 3.0

    def s_doc(self):
        self.shot("09_documento")
        self.go_tab("home")
        self.store.create_folder(self.user["id"], "Facturas")
        self.store.create_folder(self.user["id"], "Escuela")

    def s_home(self):
        self.sm.get_screen("home").refresh()
        return 0.6

    def s_docs(self):
        self.shot("10_inicio")
        self.go_tab("docs")

    def s_tools(self):
        self.shot("11_documentos")
        self.go_tab("tools")

    def s_profile(self):
        self.shot("12_herramientas")
        self.go_tab("profile")

    def s_sheet(self):
        self.shot("13_perfil")
        doc = self.store.list_documents(self.user["id"])[0]
        self.go_tab("home")
        Clock.schedule_once(lambda dt: self.doc_options(doc["id"]), 0.4)
        return 1.4

    def s_text(self):
        self.shot("14_opciones")
        for w in list(Window.children):
            if isinstance(w, main.ModalView):
                w.dismiss()
        doc = self.store.list_documents(self.user["id"])[0]
        self.sm.get_screen("text").load(None, "Factura 4521\nTotal: Q.150.00\nGracias por su compra")
        self.go("text")

    def s_end(self):
        self.shot("15_texto")
        self.toast("Recorrido terminado")
        Clock.schedule_once(lambda dt: self.stop(), 0.5)


if __name__ == "__main__":
    TourApp().run()
    print("CAPTURAS EN", OUT)
