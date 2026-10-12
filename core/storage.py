"""
Cuentas locales y biblioteca de documentos (SQLite, almacenamiento privado de la app).

Cada usuario solo ve sus propios documentos y carpetas. Las contraseñas se
guardan con PBKDF2-SHA256 + sal aleatoria (nunca en texto plano).
"""
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PAGE_SEP = "\n\n——— Página %d ———\n\n"


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)


class Store:
    def __init__(self, base_dir):
        self.base = base_dir
        os.makedirs(base_dir, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(base_dir, "scangaia.db"),
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                salt BLOB NOT NULL, hash BLOB NOT NULL, created REAL);
            CREATE TABLE IF NOT EXISTS folders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, name TEXT NOT NULL, created REAL);
            CREATE TABLE IF NOT EXISTS docs(
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                folder_id INTEGER, title TEXT NOT NULL, created REAL,
                updated REAL, pages INTEGER, texts TEXT DEFAULT '[]',
                search TEXT DEFAULT '');
            CREATE INDEX IF NOT EXISTS docs_user ON docs(user_id, updated);
        """)
        self.db.commit()

    # ------------------------------------------------------------------ util
    def _q(self, sql, args=(), one=False, commit=False):
        with self._lock:
            cur = self.db.execute(sql, args)
            if commit:
                self.db.commit()
                return cur.lastrowid
            rows = cur.fetchall()
        rows = [dict(r) for r in rows]
        return (rows[0] if rows else None) if one else rows

    # --------------------------------------------------------------- cuentas
    def create_user(self, name, email, password):
        name, email = (name or "").strip(), (email or "").strip().lower()
        if len(name) < 2:
            raise ValueError("Escribe tu nombre")
        if not EMAIL_RE.match(email):
            raise ValueError("Correo no válido")
        if len(password or "") < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres")
        if self._q("SELECT id FROM users WHERE email=?", (email,), one=True):
            raise ValueError("Ya existe una cuenta con ese correo")
        salt = os.urandom(16)
        return self._q("INSERT INTO users(name,email,salt,hash,created) VALUES(?,?,?,?,?)",
                       (name, email, salt, _hash(password, salt), time.time()), commit=True)

    def authenticate(self, email, password):
        u = self._q("SELECT * FROM users WHERE email=?",
                    ((email or "").strip().lower(),), one=True)
        if u and hmac.compare_digest(u["hash"], _hash(password or "", u["salt"])):
            return self.get_user(u["id"])
        return None

    def get_user(self, uid):
        return self._q("SELECT id,name,email,created FROM users WHERE id=?", (uid,), one=True)

    def change_password(self, uid, old, new):
        u = self._q("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if not u or not hmac.compare_digest(u["hash"], _hash(old, u["salt"])):
            raise ValueError("La contraseña actual no es correcta")
        if len(new) < 6:
            raise ValueError("La nueva contraseña debe tener al menos 6 caracteres")
        salt = os.urandom(16)
        self._q("UPDATE users SET salt=?, hash=? WHERE id=?", (salt, _hash(new, salt), uid),
                commit=True)

    def delete_user(self, uid):
        shutil.rmtree(self.user_dir(uid), ignore_errors=True)
        for t in ("docs", "folders"):
            self._q("DELETE FROM %s WHERE user_id=?" % t, (uid,), commit=True)
        self._q("DELETE FROM users WHERE id=?", (uid,), commit=True)

    # --------------------------------------------------------------- sesión
    @property
    def _session_file(self):
        return os.path.join(self.base, "session.json")

    def save_session(self, uid):
        with open(self._session_file, "w") as f:
            json.dump({"uid": uid}, f)

    def load_session(self):
        try:
            with open(self._session_file) as f:
                uid = json.load(f).get("uid")
            return uid if uid and self.get_user(uid) else None
        except (OSError, ValueError):
            return None

    def clear_session(self):
        try:
            os.remove(self._session_file)
        except OSError:
            pass

    # -------------------------------------------------------------- carpetas
    def create_folder(self, uid, name):
        name = (name or "").strip()
        if not name:
            raise ValueError("Escribe un nombre")
        return self._q("INSERT INTO folders(user_id,name,created) VALUES(?,?,?)",
                       (uid, name, time.time()), commit=True)

    def list_folders(self, uid):
        return self._q("""SELECT f.*, (SELECT COUNT(*) FROM docs d WHERE d.folder_id=f.id) AS count
                          FROM folders f WHERE user_id=? ORDER BY name COLLATE NOCASE""", (uid,))

    def rename_folder(self, fid, name):
        self._q("UPDATE folders SET name=? WHERE id=?", (name.strip(), fid), commit=True)

    def delete_folder(self, fid):
        self._q("UPDATE docs SET folder_id=NULL WHERE folder_id=?", (fid,), commit=True)
        self._q("DELETE FROM folders WHERE id=?", (fid,), commit=True)

    # ------------------------------------------------------------ documentos
    def user_dir(self, uid):
        return os.path.join(self.base, "users", str(uid))

    def doc_dir(self, doc):
        doc_id = doc["id"] if isinstance(doc, dict) else doc
        uid = doc["user_id"] if isinstance(doc, dict) else \
            self._q("SELECT user_id FROM docs WHERE id=?", (doc_id,), one=True)["user_id"]
        return os.path.join(self.user_dir(uid), "docs", doc_id)

    def new_document_folder(self, uid):
        doc_id = uuid.uuid4().hex[:16]
        path = os.path.join(self.user_dir(uid), "docs", doc_id)
        os.makedirs(path, exist_ok=True)
        return doc_id, path

    def add_document(self, uid, doc_id, title, pages, texts=None, folder_id=None):
        now = time.time()
        texts = texts or []
        self._q("""INSERT INTO docs(id,user_id,folder_id,title,created,updated,pages,texts,search)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (doc_id, uid, folder_id, title.strip() or "Documento", now, now, pages,
                 json.dumps(texts, ensure_ascii=False), " ".join(texts).lower()), commit=True)

    def list_documents(self, uid, query="", folder_id=None, limit=None):
        sql = "SELECT * FROM docs WHERE user_id=?"
        args = [uid]
        if folder_id is not None:
            sql += " AND folder_id=?"
            args.append(folder_id)
        q = (query or "").strip().lower()
        if q:
            sql += " AND (LOWER(title) LIKE ? OR search LIKE ?)"
            args += ["%" + q + "%", "%" + q + "%"]
        sql += " ORDER BY updated DESC"
        if limit:
            sql += " LIMIT %d" % int(limit)
        return self._q(sql, args)

    def get_document(self, doc_id):
        return self._q("SELECT * FROM docs WHERE id=?", (doc_id,), one=True)

    def update_document(self, doc_id, **fields):
        if "texts" in fields:
            texts = fields["texts"]
            fields["search"] = " ".join(texts).lower()
            fields["texts"] = json.dumps(texts, ensure_ascii=False)
        fields["updated"] = time.time()
        cols = ", ".join("%s=?" % k for k in fields)
        self._q("UPDATE docs SET %s WHERE id=?" % cols, list(fields.values()) + [doc_id],
                commit=True)

    def delete_document(self, doc_id):
        doc = self.get_document(doc_id)
        if doc:
            shutil.rmtree(self.doc_dir(doc), ignore_errors=True)
            self._q("DELETE FROM docs WHERE id=?", (doc_id,), commit=True)

    # ------------------------------------------------------------ archivos
    def page_paths(self, doc):
        d = self.doc_dir(doc)
        return [os.path.join(d, "page_%03d.jpg" % (i + 1)) for i in range(doc["pages"])]

    def pdf_path(self, doc):
        return os.path.join(self.doc_dir(doc), "documento.pdf")

    def thumb_path(self, doc):
        return os.path.join(self.doc_dir(doc), "thumb.jpg")

    def usage_bytes(self, uid):
        total = 0
        for root, _, files in os.walk(self.user_dir(uid)):
            total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
        return total

    @staticmethod
    def texts_of(doc):
        try:
            return json.loads(doc.get("texts") or "[]")
        except ValueError:
            return []

    @staticmethod
    def join_texts(texts):
        return "".join((PAGE_SEP % (i + 1) if len(texts) > 1 else "") + t
                       for i, t in enumerate(texts)).strip()

    @staticmethod
    def split_texts(text, pages):
        parts = re.split(r"\s*——— Página \d+ ———\s*", text or "")
        parts = [p.strip() for p in parts]
        if parts and parts[0] == "" and len(parts) == pages + 1:
            parts = parts[1:]
        if len(parts) != pages:
            return [text.strip()] + [""] * (pages - 1)
        return parts
