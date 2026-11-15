# APGE Changelog

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
