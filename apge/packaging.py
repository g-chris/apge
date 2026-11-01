"""
PyInstaller helpers so a game's .spec file stays three lines long:

    from apge.packaging import datas, hiddenimports
    a = Analysis(['app.py'], datas=datas(), hiddenimports=hiddenimports())
"""
import os

APGE_DIR = os.path.dirname(os.path.abspath(__file__))


def datas(game_dirs=('templates', 'static')):
    """Files to bundle: the game's template/static folders plus APGE's own client files."""
    result = [(d, d) for d in game_dirs if os.path.isdir(d)]
    result.append((os.path.join(APGE_DIR, 'static'), 'apge/static'))
    return result


def hiddenimports():
    # Socket.IO picks its async driver by name at runtime, so PyInstaller can't see it
    return ['engineio.async_drivers.threading', 'simple_websocket']
