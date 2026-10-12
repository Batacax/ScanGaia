# ScanGaia — escáner de documentos en Python para celular

App Android hecha 100 % en Python (**Kivy + OpenCV**) que convierte fotos de papeles en PDF limpios.

## Qué hace

| Función | Detalle |
|---|---|
| **Detección de bordes en vivo** | Marca la hoja en la cámara en tiempo real. Con **Auto** dispara sola cuando el teléfono está firme (el contorno se pone verde). |
| **Recorte preciso** | 4 esquinas arrastrables con **lupa de aumento** para colocarlas al píxel. Botones Girar, Detectar y Hoja completa. |
| **Endereza la hoja** | Corrige la perspectiva calculando la **proporción real** del papel (no lo deja estirado), con ajuste sub-píxel de esquinas. |
| **Filtros** | Mejorado (quita sombras y blanquea el papel), Original, Gris, B/N (umbral adaptativo) y Nítido. |
| **PDF real** | Varias páginas en un solo PDF, sin perder calidad, con **texto buscable/copiable** (capa invisible de OCR). |
| **OCR** | Español e inglés, **sin internet** (Google ML Kit en el teléfono). |
| **Biblioteca** | Carpetas, búsqueda por nombre **o por el texto del documento**, renombrar, mover, eliminar, añadir páginas a un documento existente. |
| **Herramientas** | Imágenes a PDF (lote con recorte automático), Importar PDF, Extraer texto, Unir documentos, Comprimir. |
| **Compartir** | WhatsApp, correo, Drive… o guardar en *Descargas/ScanGaia*. |
| **Privado** | Cuentas locales con contraseña (PBKDF2-SHA256). Todo queda en el almacenamiento privado de la app; cada cuenta ve solo lo suyo. |
| **Cámara HD** | Botón HD: usa la cámara del sistema a resolución completa para máxima nitidez. |

## Estructura

```
main.py                 Lógica de pantallas y flujo de la app
ui.kv                   Diseño (bienvenida oscura + interior claro estilo ScanGaia)
core/scanner.py         Visión: detección, perspectiva, filtros
core/pdfgen.py          Generador de PDF (sin dependencias) con capa de texto
core/ocr.py             OCR: ML Kit (Android) / Tesseract (PC)
core/storage.py         Cuentas, carpetas y documentos (SQLite)
core/platform_utils.py  Cámara, galería, compartir, Descargas, render de PDF (Android)
assets/                 Íconos Material, ícono y splash
tests/                  Pruebas del núcleo y recorrido automático de la interfaz
```

## Generar el APK

### Opción A — en la nube, sin instalar nada (recomendada)
1. Crea un repositorio en GitHub y sube esta carpeta completa (incluida `.github/`).
2. Entra a la pestaña **Actions** → *Compilar APK* → espera (~25 min la primera vez).
3. Descarga el artefacto **ScanGaia-apk**, pásalo al teléfono e instálalo.

### Opción B — en tu PC (Linux, o Windows con WSL2/Ubuntu)
```bash
sudo apt install -y git zip unzip openjdk-17-jdk autoconf libtool pkg-config \
  zlib1g-dev libncurses5-dev cmake libffi-dev libssl-dev automake
pip install buildozer==1.5.0 cython==0.29.37
cd escaner
buildozer android debug            # el APK queda en bin/
buildozer android deploy run logcat   # con el teléfono conectado por USB
```

Requisitos del teléfono: **Android 10 o superior**.

## Probar en la PC
```bash
pip install kivy==2.3.1 opencv-python numpy
pip install pytesseract pypdfium2   # opcional: OCR e importar PDF en PC
python main.py
python -m tests.test_core           # pruebas del núcleo
```

## Precisión medida (pruebas automáticas)
- Esquinas detectadas con **~1 px** de error en fotos inclinadas de 1200×1500.
- Proporción de la hoja recortada con error **< 1 %** (carta 21.6 × 27.9 cm).
- El filtro *Mejorado* sube el brillo de zonas en sombra de ~163 a ~245 (papel blanco).
