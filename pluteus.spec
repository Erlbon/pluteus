# PyInstaller spec: one file, console program (no Python needed on the machine that runs it).
#   pyinstaller pluteus.spec --noconfirm         ->  dist/pluteus.exe (Windows) or dist/pluteus (Linux)
# The tkinter admin window is left out (the web admin page and the user command line are the admin tools in the package).
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("uvicorn") + collect_submodules("starlette") + ["h11", "python_multipart", "mistune", "qrcodegen"]

a = Analysis(
    ["tools/pluteus_entry.py"],
    pathex=["."],
    datas=[("pluteus/static", "pluteus/static")],
    hiddenimports=hidden,
    excludes=["tkinter", "_tkinter", "pytest", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="pluteus",
    console=True,
    upx=False,                  # UPX shrinks the file but makes antivirus programs more suspicious
    icon="pluteus/static/icon/favicon.ico",
)
