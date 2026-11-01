# __GAME_TITLE__

A party game built on [APGE](https://github.com/g-chris/apge): one host screen, everyone else plays on their phone.

## Run it

```bash
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The host screen opens at http://localhost:8080. Players on the same Wi-Fi scan the QR code.

## Files

| File | What it is |
|---|---|
| `game.py` | The rules: phases, actions, timers, scoring |
| `templates/` + `static/` | Host (TV) and player (phone) pages |
| `tests/test_bots.py` | Bots play full games: `python tests/test_bots.py` (needs `pip install "python-socketio[client]"`) |
| `build.spec` | `pyinstaller build.spec` builds a single executable |
| `.github/workflows/release.yml` | Push a `v*` tag to build Windows/macOS/Linux downloads |
