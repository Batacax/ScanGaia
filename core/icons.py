"""Glifos de Material Icons usados por la interfaz."""
_CODES = {
    "home": 0xE88A, "folder": 0xE2C7, "folder_open": 0xE2C8, "build": 0xE869,
    "person": 0xE7FD, "scan": 0xE5FA, "add_photo": 0xE43E, "upload_file": 0xE9FC,
    "subject": 0xE8D2, "chevron_right": 0xE5CC, "chevron_left": 0xE5CB, "more": 0xE5D4,
    "search": 0xE8B6, "add": 0xE145, "new_folder": 0xE2CC, "merge": 0xE252,
    "compress": 0xE94D, "image": 0xE3F4, "download": 0xE2C4, "share": 0xE80D,
    "copy": 0xE14D, "edit": 0xE3C9, "delete": 0xE872, "rotate": 0xE41A,
    "crop_free": 0xE3C2, "auto": 0xE65F, "check": 0xE5CA, "close": 0xE5CD,
    "back": 0xE5C4, "gallery": 0xE413, "camera": 0xE412, "hd": 0xE052,
    "lock": 0xE897, "logout": 0xE9BA, "pdf": 0xE415, "file": 0xE24D,
    "description": 0xE873, "text": 0xE262, "tune": 0xE429, "save": 0xE161,
    "storage": 0xE1DB, "key": 0xE0DA, "crop": 0xE3BE,
    "shield": 0xE9E0, "drive_move": 0xE675,
}
IC = {k: chr(v) for k, v in _CODES.items()}
