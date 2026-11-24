# APGE - notes for working on the engine

APGE is a library used by separate game repos (pinned by git tag). Each game should leave
the engine better, without breaking games pinned to older tags.

Working conventions (changelog, PRs, reviews, DRY, testing) live in
apge/scaffold/CONVENTIONS.txt - the same file every new game gets. Edit it there.

## Principles (each one fixed a real bug in Hollywood Moguls - don't regress them)
- Game code never sees socket ids; players are keyed by stable `Player.id`.
- Every entry point (socket event, timer, host command) runs under `game.lock` and ends
  with one `_flush()`. New entry points must go through `PartyServer._locked`.
- Actions are phase-scoped; out-of-phase actions are rejected, not ignored silently.
- Timers are token-checked; never start a raw `threading.Timer` that touches state.
- No phase may be able to wait forever: host Skip must always do something.
- Private data goes in `player.private`; never broadcast it in public views.
- Host controls belong to the host computer. The only exception is a game's opt-in
  `vip_commands` for one VIP phone, and Skip must never be one of them.
- Everything must work offline on a LAN (no CDNs).

## Before tagging a release
- `python -m unittest discover tests` (protocol tests + harness self-tests)
- Scaffold a game (`python -m apge new /tmp/x`) and run its `tests/test_bots.py`
- Update CHANGELOG.md and `__version__` in apge/__init__.py (the only copy; pyproject.toml
  and apge.js read it), and the example `@vX.Y.Z` pins in README.md
