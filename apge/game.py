"""
Game base class: players, phases, actions, timers and per-recipient views.

A game subclasses `Game` and declares its flow with decorators:

    class MyGame(Game):
        def on_start(self):
            self.goto('answer')

        @phase('answer', timer=60)
        def enter_answer(self): ...

        @action('submit', phases='answer')
        def submit(self, player, data): ...

        @on_timeout('answer')
        def answer_timeout(self): ...

        @phase('results', timer=20, ready=True)
        def enter_results(self): ...

        @on_all_ready('results')
        def results_done(self): ...

The engine guarantees that every entry point (player action, timer expiry,
connect/disconnect, host command) runs one at a time, and that clients receive a
fresh view once that entry point finishes.
"""
import logging
import os
import threading
import time
import uuid

log = logging.getLogger('apge')

LOBBY = 'lobby'


class Reject(Exception):
    """Raise from an action handler to refuse it; the message is shown to the player."""


# ---------------------------------------------------------------------------
# Decorators. They only tag methods; Game.__init_subclass__ collects the tags.
# ---------------------------------------------------------------------------

def phase(name, timer=None, ready=False):
    """Mark a method as the `enter` hook for a phase.

    timer: seconds before the phase times out (None = waits forever, host can Skip)
    ready: a "press Continue" phase. Advances when every connected player is ready,
           or when the timer runs out; either way the @on_all_ready hook is called.
    """
    def deco(fn):
        fn._apge_phase = {'name': name, 'timer': timer, 'ready': ready}
        return fn
    return deco


def action(name, phases=None):
    """Mark a method as a player action handler: fn(self, player, data).

    phases: phase name or list of names the action is valid in. The engine rejects
            the action in any other phase, so late or duplicate taps can't re-run a
            transition. None means "any phase except the lobby".
    """
    if isinstance(phases, str):
        phases = [phases]

    def deco(fn):
        fn._apge_action = {'name': name, 'phases': phases}
        return fn
    return deco


def _phase_hook(kind):
    def decorator(phase_name):
        def deco(fn):
            fn._apge_hook = (kind, phase_name)
            return fn
        return deco
    return decorator


on_timeout = _phase_hook('timeout')
"""Called when a phase's timer runs out (and by the host's Skip button)."""

on_all_ready = _phase_hook('all_ready')
"""Called when a ready=True phase completes (everyone ready, timer, or Skip)."""

on_skip = _phase_hook('skip')
"""Optional: what the host's Skip button does in this phase (default: same as timeout)."""


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

class Player:
    """One studio/team/person. `id` is stable for the whole session, across reconnects."""

    def __init__(self, name, token):
        self.id = uuid.uuid4().hex[:12]
        self.name = name
        self.token = token
        self.connected = True
        self.ready = False
        self.public = {}    # visible to everyone (and the host screen)
        self.private = {}   # visible only to this player (and the server)

    def to_public(self):
        return {'id': self.id, 'name': self.name, 'connected': self.connected,
                'ready': self.ready, **self.public}

    def __repr__(self):
        return f'<Player {self.name} {self.id}>'


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------

class Game:
    """Base class for an APGE game. Override the on_* hooks and declare phases/actions."""

    title = 'APGE Game'
    min_players = 1
    max_players = 12
    allow_late_join = False   # may new names join after the game has started?
    end_phase = 'game_over'   # phase that means "this game is finished"
    max_name = 24             # longest player name the join form and server accept
    # Host commands a player's phone may send when that player is the VIP (see vip()),
    # e.g. ('start', 'reset', 'quit'). Skip stays with the host computer so a phone
    # can never rush everyone else.
    vip_commands = ()

    # Collected from decorators by __init_subclass__
    _phases = {}
    _actions = {}
    _hooks = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        phases, actions, hooks = {}, {}, {}
        # Walk the MRO so a game can subclass another game
        for klass in reversed(cls.__mro__):
            for attr in vars(klass).values():
                if hasattr(attr, '_apge_phase'):
                    spec = dict(attr._apge_phase, enter=attr)
                    phases[spec['name']] = spec
                if hasattr(attr, '_apge_action'):
                    spec = dict(attr._apge_action, fn=attr)
                    actions[spec['name']] = spec
                if hasattr(attr, '_apge_hook'):
                    hooks[attr._apge_hook] = attr
        cls._phases, cls._actions, cls._hooks = phases, actions, hooks

    def __init__(self):
        self.players = {}        # {player_id: Player}, in join order
        self.phase = LOBBY
        self.public = {}         # game state everyone can see
        self.timer = None        # {'token', 'duration', 'started'} or None
        self.version = 0         # bumps on every change; clients and tests use it
        self.games_played = 0
        self.lock = threading.RLock()
        self._dirty = True
        self._goto_depth = 0
        self.time_scale = float(os.environ.get('APGE_TIME_SCALE', '1'))
        self._server = None      # set by PartyServer

    # ---- hooks for games to override -------------------------------------

    def on_start(self):
        """Host pressed Start. Initialise game state and goto() the first phase."""
        raise NotImplementedError

    def on_reset(self):
        """Back to the lobby for a new game. Default: clear self.public and player data."""
        self.public = {}
        for p in self.players.values():
            p.public, p.private = {}, {}

    def on_player_joined(self, player):
        pass

    def on_player_disconnected(self, player):
        pass

    def on_player_reconnected(self, player):
        pass

    def vip(self):
        """The player whose phone may send `vip_commands`. Default: first connected player.

        Override to choose differently (e.g. the first player to finish setting up).
        """
        return next(iter(self.connected_players()), None)

    def lobby_ready(self):
        """True when everyone has finished any setup they do in the lobby.

        Only advisory: the host can still Start (on_start may Reject). Shown to screens as
        `lobby_ready`, and the bot harness waits for it before pressing Start.
        """
        return True

    def on_quit(self):
        """Host ended the session: every player is about to be removed. Clear game state."""
        self.public = {}

    def host_view(self):
        """What the host/TV screen receives. Default: the public view."""
        return self.public_view()

    def player_view(self, player):
        """What one player's phone receives. Default: public view + their private data."""
        view = self.public_view()
        view['me'] = {'id': player.id, 'name': player.name, 'ready': player.ready,
                      **player.public, 'private': player.private}
        return view

    def public_view(self):
        """Everything safe for every screen to see."""
        spec = self._phases.get(self.phase, {})
        return {
            'title': self.title,
            'phase': self.phase,
            'end_phase': self.end_phase,
            'ready_check': bool(spec.get('ready')),
            'timer': self.timer_view(),
            'players': [p.to_public() for p in self.players.values()],
            'min_players': self.min_players,
            'max_players': self.max_players,
            'max_name': self.max_name,
            'vip_id': self._vip_id(),
            'lobby_ready': self.phase == LOBBY and bool(self.lobby_ready()),
            'version': self.version,
            'data': self.public,
        }

    # ---- helpers for games ------------------------------------------------

    def goto(self, phase_name, timer=None):
        """Enter a phase: clears ready flags, (re)starts its timer, calls its enter hook.

        timer: override the phase's declared timer for this visit.
        """
        if phase_name != LOBBY and phase_name != self.end_phase and phase_name not in self._phases:
            raise ValueError(f'Unknown phase {phase_name!r}; declare it with @phase')
        self._goto_depth += 1
        if self._goto_depth > 50:
            raise RuntimeError('goto() recursion - does a phase enter hook goto() itself?')
        try:
            log.info('phase %s -> %s', self.phase, phase_name)
            self.phase = phase_name
            for p in self.players.values():
                p.ready = False
            self.cancel_timer()
            spec = self._phases.get(phase_name)
            duration = timer if timer is not None else (spec or {}).get('timer')
            if duration:
                self.start_timer(duration)
            self.mark_dirty()
            if spec:
                spec['enter'](self)
        finally:
            self._goto_depth -= 1

    def start_timer(self, seconds):
        """Start (or replace) the phase timer. Expiry calls the phase's timeout handling."""
        token = uuid.uuid4().hex
        self.timer = {'token': token, 'duration': seconds, 'started': time.time()}
        t = threading.Timer(seconds * self.time_scale, self._timer_fired, args=[token])
        t.daemon = True
        t.start()
        self.mark_dirty()

    def cancel_timer(self):
        # The thread can't be stopped; clearing the token makes it a no-op when it fires
        if self.timer:
            self.timer = None
            self.mark_dirty()

    def timer_view(self):
        if not self.timer:
            return None
        elapsed = (time.time() - self.timer['started']) / self.time_scale
        return {'token': self.timer['token'], 'duration': self.timer['duration'],
                'remaining': max(0.0, self.timer['duration'] - elapsed)}

    def connected_players(self):
        return [p for p in self.players.values() if p.connected]

    def waiting_on(self, done):
        """Players for whom done(player) is False. Use to decide when a phase is complete."""
        return [p for p in self.players.values() if not done(p)]

    def everyone(self, done, include_disconnected=True):
        """True if done(player) holds for every player (optionally only connected ones)."""
        pool = self.players.values() if include_disconnected else self.connected_players()
        return all(done(p) for p in pool)

    def send(self, player, event, data=None):
        """Send a one-off message (toast, sound cue...) to one player."""
        if self._server:
            self._server.send_to_player(player, event, data)

    def broadcast(self, event, data=None):
        """Send a one-off message to every screen."""
        if self._server:
            self._server.send_to_all(event, data)

    def mark_dirty(self):
        self._dirty = True

    def player_by_name(self, name):
        return next((p for p in self.players.values() if p.name == name), None)

    # ---- engine internals (called by PartyServer under self.lock) --------

    def _vip_id(self):
        if not self.vip_commands:
            return None
        player = self.vip()
        return player.id if player else None

    def _quit(self):
        """Back to the lobby with nobody in it. Returns the removed players' ids."""
        self.cancel_timer()
        self.phase = LOBBY
        self.on_quit()
        removed = list(self.players)
        self.players = {}
        self.mark_dirty()
        return removed

    def _timer_fired(self, token):
        with self.lock:
            if not self.timer or self.timer['token'] != token:
                return  # cancelled, or superseded by a newer timer
            self.timer = None
            log.info('timer expired in %s', self.phase)
            try:
                self._expire_phase()
            except Exception:
                # A bug in a timeout hook must not leave clients without an update
                log.exception('error in timeout handling for phase %s', self.phase)
            self._flush()

    def _expire_phase(self):
        hook = self._hooks.get(('timeout', self.phase))
        spec = self._phases.get(self.phase, {})
        if hook:
            hook(self)
        elif spec.get('ready'):
            self._complete_ready()
        self.mark_dirty()

    def _skip(self):
        """Host Skip: phase's @on_skip, else behave as if the timer ran out."""
        hook = self._hooks.get(('skip', self.phase))
        if hook:
            hook(self)
        else:
            self.cancel_timer()
            self._expire_phase()
        self.mark_dirty()

    def _complete_ready(self):
        phase_now = self.phase
        hook = self._hooks.get(('all_ready', phase_now))
        if hook:
            hook(self)
        else:
            log.warning('ready phase %s has no @on_all_ready hook', phase_now)

    def _set_ready(self, player):
        spec = self._phases.get(self.phase, {})
        if not spec.get('ready'):
            raise Reject('Nothing to continue right now.')
        if player.ready:
            return
        player.ready = True
        self.mark_dirty()
        self._check_ready_complete()

    def _check_ready_complete(self):
        # Disconnected players don't hold up a Continue screen - the timer covers them anyway
        if not self._phases.get(self.phase, {}).get('ready') or not self.connected_players():
            return
        if self.everyone(lambda p: p.ready, include_disconnected=False):
            self.cancel_timer()
            self._complete_ready()

    def _dispatch(self, player, name, data):
        spec = self._actions.get(name)
        if not spec:
            raise Reject(f'Unknown action {name!r}.')
        allowed = spec['phases']
        if allowed is None:
            if self.phase == LOBBY:
                raise Reject("The game hasn't started yet.")
        elif self.phase not in allowed:
            # Usually a late tap after a timer moved the game on - harmless
            raise Reject('Too late - the game has moved on.')
        result = spec['fn'](self, player, data if data is not None else {})
        self.mark_dirty()
        return result

    def _start(self):
        if self.phase != LOBBY:
            raise Reject('The game has already started.')
        if len(self.players) < self.min_players:
            raise Reject(f'Need at least {self.min_players} players to start.')
        self.on_start()
        self.mark_dirty()

    def _reset(self):
        self.cancel_timer()
        self.phase = LOBBY
        for p in self.players.values():
            p.ready = False
        self.on_reset()
        self.mark_dirty()

    def _flush(self):
        if self._dirty:
            self._dirty = False
            self.version += 1
            if self._server:
                self._server.push_views()
