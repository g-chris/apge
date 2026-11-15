# APGE Changelog

## 0.2.0 (2026-11-15)

Added while building Down Under Gambler's Sweat It Out! Derby '27, whose lobby has players
draw an avatar and spend skill points, and whose first finished player runs the game.
Everything is opt-in: games on 0.1.x can bump their pin without code changes.

### New Features
- **VIP phone controls**: `Game.vip_commands` (e.g. `('start', 'reset', 'quit')`) lets one
  player's phone send those host commands. `Game.vip()` picks the player (default: first
  connected player; override it, e.g. first to finish their character). Views carry
  `vip_id`; `apge.isVip()` on the phone. Skip is never a VIP command by design, and the
  host computer keeps every control
- **Quit**: new `quit` host command (`apge.quit()`, and an "End game" button in the host
  panel at game over) goes back to the lobby and removes every player. Phones return to
  the join screen with a message. `Game.on_quit()` hook
- **Per-game name length**: `Game.max_name` (default 24) is enforced by the server and the
  join form; views carry `max_name`
- **Lobby setup**: `Game.lobby_ready()` hook says whether everyone has finished lobby setup
  (shown to screens as `lobby_ready`). The bot harness now waits for it, up to
  `run_scenario(start_wait=5.0)` seconds, before pressing Start - and after New Game - so
  bots can do lobby setup in `decide()` like any other phase

### Bug Fixes
- **Couldn't scaffold into a fresh repo** (MEDIUM)
  - Issue: `python -m apge new .` refused a directory holding only `.git` (or a venv)
  - Root cause: any directory entry counted as "not empty"
  - Solution: `.git`, `venv`, `.venv`, `LICENSE`, `.gitattributes` and `.DS_Store` are ignored
  - Files modified: apge/__main__.py
- **Executable named "Gambler'S" style slugs** (LOW)
  - Issue: a title like "Gambler's Derby" produced the slug `GamblerSDerby`
  - Root cause: `str.title()` capitalises the letter after an apostrophe
  - Solution: apostrophes are dropped before title-casing
  - Files modified: apge/__main__.py

### Packaging / Internal
- Kick and quit share one player-removal path in the server (`_unbind`)
- Engine tests: VIP rules, quit, name length, lobby_ready, the harness waiting for lobby
  setup (and starting anyway when it never finishes), scaffolding into a git repo

## 0.1.1 (2026-11-15)

- New games get `CONVENTIONS.txt` (changelog format, versions and releases, branches,
  PR template and review checklist, reusable/DRY code, APGE rules, testing). It lives
  in apge/scaffold/ as the single copy; the engine's CLAUDE.md points to it
- The version is defined once (`apge.__version__`): pyproject.toml reads it, and
  apge.js gets it from the server instead of a hard-coded copy
- No API changes: games on 0.1.0 can bump their pin without code changes

## 0.1.0 (2026-11-01)

First release, extracted from Hollywood Moguls 1.0.1 and redesigned around what its
virtual-user testing found.

- `Game` base class: phases (`@phase`), phase-scoped actions (`@action`), timers with
  stale-thread tokens, Continue screens (`ready=True` + `@on_all_ready`), host Skip
- Stable player ids, per-browser reconnect tokens, duplicate-name and late-join refusal,
  second-tab takeover, lobby kick
- Single game lock around every entry point; one view push per change
- Per-recipient views: public / per-player private / host
- Host controls only for the machine running the game (`APGE_REMOTE_HOST=1` to relax)
- `apge.js` client: auto-rejoin, connection badge, acked actions with toasts, countdowns,
  screen switching, join form, host panel, realtime relay channel
- `apge.testing`: bots with idle/slow/flaky/double-tap/chaos/quit personalities,
  scenario matrix, stall/error/orphan/invariant detection
- `apge.packaging` PyInstaller helpers; `python -m apge new` starter game with bot
  tests, build spec and release workflow
