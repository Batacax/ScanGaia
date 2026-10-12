"""
Generador de PDF sin dependencias externas.

Inserta las páginas JPEG tal cual (sin recomprimir, máxima calidad) y,
opcionalmente, una capa de texto invisible con el resultado del OCR para
que el PDF se pueda buscar y copiar en cualquier visor.
"""
import datetime
import struct

A4_W = 595.28  # puntos


def jpeg_size(data):
    """Devuelve (ancho, alto, componentes) leyendo el marcador SOF del JPEG."""
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            comps = data[i + 9]
            return w, h, comps
        i += 2 + seg_len
    raise ValueError("JPEG inválido")


def _pdf_str(text):
    raw = text.encode("cp1252", errors="replace")
    raw = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    raw = raw.replace(b"\r", b"").replace(b"\t", b" ")
    return b"(" + raw + b")"


def _info_str(text):
    # Cadena UTF-16BE con BOM para metadatos (soporta tildes y ñ)
    data = b"\xfe\xff" + text.encode("utf-16-be")
    return b"<" + data.hex().encode() + b">"


def _text_layer(text, w, h):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return b""
    step = (h - 40) / max(len(lines), 1)
    size = max(4.0, min(11.0, step * 0.8))
    out = [b"BT 3 Tr /F1 %.2f Tf" % size]
    y = h - 20 - size
    for line in lines:
        out.append(b"1 0 0 1 20 %.2f Tm %s Tj" % (y, _pdf_str(line)))
        y -= step
    out.append(b"ET")
    return b"\n".join(out) + b"\n"


def build_pdf(image_paths, out_path, texts=None, title="Documento"):
    objects = []  # lista de bytes; el índice + 1 es el número de objeto

    def add(obj):
        objects.append(obj)
        return len(objects)

    catalog = add(None)
    pages_obj = add(None)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
               b"/Encoding /WinAnsiEncoding >>")
    kids = []
    for idx, path in enumerate(image_paths):
        with open(path, "rb") as f:
            data = f.read()
        w, h, comps = jpeg_size(data)
        cs = b"/DeviceGray" if comps == 1 else b"/DeviceRGB"
        img = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                  b"/ColorSpace %s /BitsPerComponent 8 /Filter /DCTDecode "
                  b"/Length %d >>\nstream\n" % (w, h, cs, len(data)) + data + b"\nendstream")
        pw = A4_W
        ph = A4_W * h / float(w)
        content = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q\n" % (pw, ph)
        if texts and idx < len(texts):
            content += _text_layer(texts[idx], pw, ph)
        cont = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
        page = add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                   b"/Resources << /XObject << /Im0 %d 0 R >> /Font << /F1 %d 0 R >> >> "
                   b"/Contents %d 0 R >>" % (pages_obj, pw, ph, img, font, cont))
        kids.append(page)

    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    objects[pages_obj - 1] = (b"<< /Type /Pages /Kids [" +
                              b" ".join(b"%d 0 R" % k for k in kids) +
                              b"] /Count %d >>" % len(kids))
    now = datetime.datetime.now().strftime("D:%Y%m%d%H%M%S")
    info = add(b"<< /Title " + _info_str(title) + b" /Producer (ScanGaia) "
               b"/Creator (ScanGaia) /CreationDate (" + now.encode() + b") >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, catalog, info, xref))
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(out)
    import os
    os.replace(tmp, out_path)
    return out_path
