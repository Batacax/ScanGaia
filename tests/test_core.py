"""Pruebas del núcleo: ejecutar con  python -m tests.test_core  desde la carpeta del proyecto."""
import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import scanner, pdfgen, storage  # noqa: E402


def synthetic_photo(seed=0):
    """Hoja con texto, en perspectiva, sobre una mesa con textura y sombra."""
    rng = np.random.default_rng(seed)
    page = np.full((1100, 850, 3), 245, np.uint8)
    for i in range(22):
        cv2.putText(page, "Linea de prueba %02d - Factura 4521 Q.150.00" % i,
                    (50, 80 + i * 44), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (30, 30, 30), 2)
    cv2.rectangle(page, (40, 40), (810, 1060), (60, 60, 200), 2)

    H, W = 1500, 1200
    table = rng.integers(60, 110, (H, W, 3), dtype=np.uint8)
    table = cv2.GaussianBlur(table, (0, 0), 3)
    # Proyección física: hoja de 21.6 x 27.9 cm inclinada frente a una cámara real
    f = 1300.0
    ax, ay, az = np.radians([28 + seed * 4, -15 + seed * 6, 7])
    Rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    Ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]])
    Rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    corners3d = np.array([[-10.8, -13.95, 0], [10.8, -13.95, 0], [10.8, 13.95, 0], [-10.8, 13.95, 0]])
    cam = (Rz @ Ry @ Rx @ corners3d.T).T + np.array([0.3, 0.5, 42.0])
    true_quad = np.stack([f * cam[:, 0] / cam[:, 2] + W / 2,
                          f * cam[:, 1] / cam[:, 2] + H / 2], axis=1).astype(np.float32)
    src = np.array([[0, 0], [849, 0], [849, 1099], [0, 1099]], np.float32)
    M = cv2.getPerspectiveTransform(src, true_quad)
    warped = cv2.warpPerspective(page, M, (W, H))
    mask = cv2.warpPerspective(np.full(page.shape[:2], 255, np.uint8), M, (W, H))
    photo = np.where(mask[..., None] > 0, warped, table)
    # Sombra diagonal
    grad = np.tile(np.linspace(0.55, 1.0, W, dtype=np.float32), (H, 1))[..., None]
    photo = np.clip(photo.astype(np.float32) * grad + rng.normal(0, 4, photo.shape), 0, 255)
    return photo.astype(np.uint8), true_quad


def test_detection():
    for seed in range(3):
        photo, true_quad = synthetic_photo(seed)
        quad, conf = scanner.detect_document(photo)
        err = np.linalg.norm(quad - true_quad, axis=1).max()
        print("  seed %d: confianza=%.2f  error máx esquina=%.1f px" % (seed, conf, err))
        assert conf > 0.5, conf
        assert err < 12, err
    return photo, quad


def test_pipeline(tmp):
    photo, quad = test_detection()
    warped = scanner.warp(photo, quad)
    h, w = warped.shape[:2]
    print("  recorte: %dx%d (aspecto %.3f, real %.3f)" % (w, h, h / w, 1100 / 850))
    assert abs(h / w - 1100 / 850) < 0.06
    paths = []
    for mode, name in scanner.FILTERS:
        out = scanner.enhance(warped, mode)
        assert out.shape == warped.shape and out.dtype == np.uint8
        p = os.path.join(tmp, "page_%s.jpg" % mode)
        scanner.save_jpeg(out, p)
        paths.append(p)
    # El filtro "Mejorado" debe aclarar la zona sombreada (izquierda)
    magic = scanner.enhance(warped, "magic")
    left = lambda im: float(np.median(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)[:, : w // 6]))
    print("  brillo zona sombreada: original=%.0f  mejorado=%.0f" % (left(warped), left(magic)))
    assert left(magic) > left(warped) + 25
    cv2.imwrite(os.path.join(tmp, "foto.jpg"), photo)

    pdf = os.path.join(tmp, "doc.pdf")
    pdfgen.build_pdf(paths, pdf, texts=["Factura 4521 ñandú ÁÉÍ"] + [""] * (len(paths) - 1),
                     title="Prueba ñ")
    data = open(pdf, "rb").read()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    try:
        from pypdf import PdfReader
        r = PdfReader(pdf)
        assert len(r.pages) == len(paths)
        txt = r.pages[0].extract_text()
        print("  PDF: %d páginas, texto buscable: %r" % (len(r.pages), txt.strip()))
        assert "Factura 4521" in txt
    except ImportError:
        print("  (pypdf no instalado; validación básica del PDF)")
    return pdf


def test_storage(tmp):
    st = storage.Store(os.path.join(tmp, "data"))
    uid = st.create_user("Ana", "ana@correo.com", "secreto1")
    try:
        st.create_user("Ana2", "ANA@correo.com", "otra123")
        raise AssertionError("debió rechazar correo duplicado")
    except ValueError:
        pass
    assert st.authenticate("ana@correo.com", "malo") is None
    u = st.authenticate("Ana@Correo.com", "secreto1")
    assert u and u["id"] == uid
    doc_id, folder = st.new_document_folder(uid)
    st.add_document(uid, doc_id, "Recibo luz", 2, ["texto uno", "Factura dos"])
    assert len(st.list_documents(uid)) == 1
    assert len(st.list_documents(uid, "factura")) == 1
    assert len(st.list_documents(uid, "nada")) == 0
    uid2 = st.create_user("Beto", "beto@correo.com", "123456")
    assert st.list_documents(uid2) == []          # privacidad entre cuentas
    st.save_session(uid)
    assert st.load_session() == uid
    st.delete_document(doc_id)
    assert not os.path.exists(folder)
    print("  cuentas, búsqueda y privacidad OK")


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    print("Detección y pipeline:")
    test_pipeline(tmp)
    print("Almacenamiento:")
    test_storage(tmp)
    print("\nTODO OK  ->", tmp)
