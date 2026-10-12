[app]
title = ScanGaia
package.name = scangaia
package.domain = org.scangaia
source.dir = .
source.include_exts = py,kv,png,jpg,ttf
source.exclude_dirs = tests,bin,.buildozer,__pycache__
version = 1.0.0

# Kivy (interfaz), OpenCV + NumPy (visión), pyjnius/android (cámara, OCR, compartir)
requirements = python3,kivy==2.3.1,numpy,opencv,pyjnius,android,sqlite3

icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #070B12
orientation = portrait
fullscreen = 0

android.permissions = CAMERA
android.api = 34
android.minapi = 29
android.ndk_api = 29
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.enable_androidx = True

# OCR en el dispositivo (español e inglés, sin internet) con Google ML Kit
android.gradle_dependencies = com.google.mlkit:text-recognition:16.0.1

p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 1
