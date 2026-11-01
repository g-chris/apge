# PyInstaller spec for __GAME_TITLE__ - builds one self-contained executable.
#   pip install pyinstaller && pyinstaller build.spec    (output in dist/)
import sys

from apge.packaging import datas, hiddenimports

a = Analysis(['app.py'], datas=datas(), hiddenimports=hiddenimports(), excludes=['tkinter'])
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='__GAME_SLUG__',
    console=True,   # the console window shows the join address and keeps the server running
    upx=False,
    target_arch='arm64' if sys.platform == 'darwin' else None,
)
