# PyInstaller spec (no Python needed on the machine that runs the result). Two builds:
#   pyinstaller pluteus.spec --noconfirm
#       ->  dist/pluteus.exe (Windows) or dist/pluteus (Linux): one file, a console program; the portable download.
#   set PLUTEUS_BUILD=folder  &  pyinstaller pluteus.spec --noconfirm --workpath build/folder
#       ->  dist/pluteus/ with pluteus.exe (the console command line) and pluteus-tray.exe (windowed: the tray, pluteus/tray.py)
#           sharing one _internal folder. This is what the Windows installer (installer/pluteus.iss) installs. A folder build
#           also starts faster: the one-file exe unpacks itself at every start.
# The tkinter admin window is left out (the web admin page and the user command line are the admin tools in the package).
import os

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("uvicorn") + collect_submodules("starlette") + ["h11", "python_multipart", "mistune", "qrcodegen"]
datas = [("pluteus/static", "pluteus/static"), ("pluteus/CHANGELOG.md", "pluteus")]
excludes = ["tkinter", "_tkinter", "pytest", "matplotlib", "numpy"]
icon = "pluteus/static/icon/favicon.ico"


def analysis(script):
    return Analysis([script], pathex=["."], datas=datas, hiddenimports=hidden, excludes=excludes, noarchive=False)


if os.environ.get("PLUTEUS_BUILD") == "folder":
    cli = analysis("tools/pluteus_entry.py")
    tray = analysis("tools/pluteus_tray_entry.py")
    cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="pluteus", console=True, upx=False, icon=icon)
    tray_exe = EXE(PYZ(tray.pure), tray.scripts, [], exclude_binaries=True, name="pluteus-tray", console=False, upx=False, icon=icon)
    coll = COLLECT(cli_exe, cli.binaries, cli.datas, tray_exe, tray.binaries, tray.datas, upx=False, name="pluteus")
else:
    a = analysis("tools/pluteus_entry.py")
    exe = EXE(
        PYZ(a.pure), a.scripts, a.binaries, a.datas, [],
        name="pluteus",
        console=True,
        upx=False,                  # UPX shrinks the file but makes antivirus programs more suspicious
        icon=icon,
    )
