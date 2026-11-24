"""
Virtual-user testing for APGE games.

Bots join a real in-process server and play whole games over Socket.IO, with timers
sped up. The engine-level personalities (idle, slow, flaky reconnects, double taps,
chaos replays, quitting mid-game) are built in; a game only writes `decide()`.

    from apge.testing import Bot, run_matrix, standard_scenarios

    class MyBot(Bot):
        def decide(self, view):
            if view['phase'] == 'answer' and 'answer' not in view['me']['private']:
                return [('submit', {'text': 'hello'})]

    ok = run_matrix(MyGame, standard_scenarios(MyBot, ghost_phases=['answer']), seeds=3)

A run fails on: a stall (no state change for `stall` seconds), any server-side error
logged by the engine or game, a bot left without a player view, or a failed
`invariant(game)` check. Rejected actions ("Too late - the game has moved on") are
normal and don't fail a run.
"""
import logging
import random
import socket
import sys
import threading
import time
import traceback

try:
    import socketio
except ImportError:  # pragma: no cover
    raise ImportError('apge.testing needs the Socket.IO client: pip install "apge[testing]"')

from werkzeug.serving import make_server

from .game import LOBBY
from .server import PartyServer

NOISE = ('code 400', 'packet queue is empty', 'Bad request')


class _ErrorLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.errors = []

    def emit(self, record):
        msg = record.getMessage()
        if any(n in msg for n in NOISE):
            return  # websocket teardown chatter from test clients
        if record.exc_info:
            msg += '\n' + ''.join(traceback.format_exception(*record.exc_info))
        self.errors.append(msg)


_error_log = _ErrorLog()
logging.getLogger().addHandler(_error_log)
for noisy in ('werkzeug', 'engineio', 'socketio', 'engineio.client', 'socketio.client'):
    logging.getLogger(noisy).setLevel(logging.CRITICAL)


def _thread_excepthook(args):
    _error_log.errors.append('thread exception: ' + ''.join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


threading.excepthook = _thread_excepthook


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestServer:
    """Run a game on a background thread. Use as a context manager."""

    def __init__(self, game, time_scale=0.05, base_dir='.'):
        self.game = game
        game.time_scale = time_scale
        self.port = free_port()
        self.party = PartyServer(game, base_dir=base_dir, port=self.port)
        self.url = f'http://127.0.0.1:{self.port}'
        self._httpd = make_server('127.0.0.1', self.port, self.party.app, threaded=True)

    def __enter__(self):
        _error_log.errors = []
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()

    @property
    def errors(self):
        return list(_error_log.errors)

    def host_client(self):
        """A connected host-screen client with host powers."""
        host = socketio.Client(reconnection=False)
        host.connect(self.url, transports=['websocket'])
        host.call('apge:host', {'key': self.party.host_key}, timeout=5)
        return host


class Bot:
    """A virtual player. Subclass and implement decide(view).

    Personality knobs (all optional):
        delay       (min, max) seconds before reacting to a new state
        idle        never act or press Continue - only timers move them along
        flaky       chance per state update of dropping and rejoining 0.2-2s later
        double_tap  chance of sending each action twice
        chaos       chance per state update of replaying an old action (out of phase)
        quit_in     phase name: leave for good the first time it's reached
    """

    def __init__(self, name, url, rng, delay=(0.0, 0.4), idle=False, flaky=0.0,
                 double_tap=0.0, chaos=0.0, quit_in=None):
        self.name, self.url, self.rng = name, url, rng
        self.delay, self.idle, self.flaky = delay, idle, flaky
        self.double_tap, self.chaos, self.quit_in = double_tap, chaos, quit_in
        self.token = f'bot-{name}-{rng.random()}'
        self.view = None
        self.gone = False         # quit for good
        self.offline = False
        self.reconnects = 0
        self.history = []         # actions sent, for chaos replays
        self._lock = threading.Lock()
        self._connect()

    # ---- override ----------------------------------------------------------

    def decide(self, view):
        """Return a list of (action_name, data) to send for this view, or None.

        Called once per state change (after `delay`). view['me'] is this bot's player.
        Base class already handles Continue screens (ready_check phases).
        """
        return None

    # ---- machinery ---------------------------------------------------------

    def _connect(self):
        self.sio = socketio.Client(reconnection=False)
        self.sio.on('apge:state', self._on_state)
        transports = ['websocket'] if self.rng.random() < 0.5 else None  # exercise both
        self.sio.connect(self.url, transports=transports)
        ack = self.sio.call('apge:join', {'name': self.name, 'token': self.token}, timeout=5)
        if not ack.get('ok'):
            raise RuntimeError(f'{self.name} could not join: {ack}')

    def _drop(self, permanent):
        self.offline = True
        try:
            self.sio.disconnect()
        except Exception:
            pass
        if permanent:
            self.gone = True
            return

        def back():
            if self.gone:
                return
            self.reconnects += 1
            try:
                self._connect()
            finally:
                self.offline = False
        threading.Timer(self.rng.uniform(0.2, 2.0), back).start()

    def _on_state(self, view):
        if self.gone or self.offline:
            return
        self.view = view
        if 'me' not in view:
            return
        if self.quit_in and view['phase'] == self.quit_in:
            self._drop(permanent=True)
            return
        if self.flaky and view['phase'] != LOBBY and self.rng.random() < self.flaky:
            self._drop(permanent=False)
            return
        threading.Timer(self.rng.uniform(*self.delay), self._act, args=[view]).start()

    def _send(self, name, data):
        try:
            self.sio.emit('apge:action', {'name': name, 'data': data})
        except Exception:
            pass

    def _act(self, view):
        with self._lock:
            # A newer state arrived while we waited - react to that one instead
            if self.gone or self.offline or self.view is None or self.view['version'] != view['version']:
                return
            if self.idle:
                return
            if self.chaos and self.history and self.rng.random() < self.chaos:
                self._send(*self.rng.choice(self.history))
            if view.get('ready_check') and not view['me'].get('ready'):
                try:
                    self.sio.emit('apge:ready', {})
                except Exception:
                    pass
            for name, data in (self.decide(view) or []):
                self.history.append((name, data))
                self._send(name, data)
                if self.double_tap and self.rng.random() < self.double_tap:
                    self._send(name, data)

    def close(self):
        self.gone = True
        try:
            self.sio.disconnect()
        except Exception:
            pass


class Result:
    def __init__(self):
        self.passed = False
        self.reason = ''
        self.errors = []
        self.violations = []
        self.phases = []
        self.reconnects = 0
        self.seconds = 0.0

    def summary(self):
        lines = [f"{'PASS' if self.passed else 'FAIL'} {self.reason} "
                 f"({self.seconds:.0f}s, {self.reconnects} reconnects)"]
        if not self.passed:
            lines.append('  last phases: ' + ' > '.join(self.phases[-8:]))
            lines += ['  VIOLATION: ' + v for v in sorted(set(self.violations))[:8]]
            lines += ['  ERROR: ' + e.strip()[-1500:] for e in self.errors[:3]]
        return '\n'.join(lines)


def _start_when_ready(host, game, wait, result):
    """Press Start once game.lobby_ready() (lobby setup done), or after `wait` seconds anyway -
    like a host who gets impatient with someone still drawing their avatar."""
    deadline = time.time() + wait
    while time.time() < deadline:
        with game.lock:
            if game.phase == LOBBY and game.lobby_ready():
                break
        time.sleep(0.05)
    ack = host.call('apge:host_command', {'command': 'start'}, timeout=5)
    if not ack.get('ok'):
        result.violations.append(f'start refused: {ack}')


def run_scenario(make_game, bots, seed=0, games=1, time_scale=0.05, stall=20.0,
                 host_skip_after=None, invariant=None, timeout=300.0, start_wait=5.0):
    """Play `games` full games with the given bots. Returns a Result.

    make_game:        zero-arg callable returning a fresh Game
    bots:             list of (BotClass, knobs_dict)
    host_skip_after:  if set, the host presses Skip when nothing has changed for this
                      many seconds (simulates a host rescuing the game from a ghost)
    invariant:        callable(game) run under the game lock; raise AssertionError to fail
    start_wait:       longest to wait for game.lobby_ready() before pressing Start (bots do
                      their lobby setup, e.g. drawing an avatar, in decide() like any phase)
    """
    rng = random.Random(seed)
    result = Result()
    started = time.time()
    game = make_game()

    with TestServer(game, time_scale=time_scale) as server:
        host = server.host_client()
        players = []
        for i, (cls, knobs) in enumerate(bots):
            players.append(cls(f'Bot{i}', server.url, random.Random(rng.random()), **knobs))
        time.sleep(0.3)

        games_done = 0
        _start_when_ready(host, game, start_wait, result)

        last_version, last_change, last_phase = None, time.time(), None
        result.reason = 'timeout'
        while time.time() - started < timeout:
            time.sleep(0.1)
            with game.lock:
                version, phase = game.version, game.phase
                if invariant:
                    try:
                        invariant(game)
                    except AssertionError as e:
                        result.violations.append(f'invariant in {phase}: {e}')
            if phase != last_phase:
                result.phases.append(phase)
                last_phase = phase
            if version != last_version:
                last_version, last_change = version, time.time()
            idle_for = time.time() - last_change

            if phase == game.end_phase:
                games_done += 1
                if games_done >= games:
                    result.reason = f'completed {games} game(s)'
                    break
                time.sleep(0.3)
                host.call('apge:host_command', {'command': 'reset'}, timeout=5)
                if game.phase != LOBBY:
                    result.violations.append(f'reset left the game in {game.phase}')
                _start_when_ready(host, game, start_wait, result)
                last_change = time.time()
                continue
            if host_skip_after and idle_for > host_skip_after and phase != LOBBY:
                host.call('apge:host_command', {'command': 'skip'}, timeout=5)
                last_change = time.time()
                continue
            if idle_for > stall:
                result.reason = f'stalled in {phase}'
                break

        time.sleep(0.5)
        for bot in players:
            if not bot.gone and not bot.offline and (bot.view is None or 'me' not in bot.view):
                result.violations.append(f'{bot.name} has no player view (orphaned)')
        result.reconnects = sum(b.reconnects for b in players)
        for bot in players:
            bot.close()
        host.disconnect()
        result.errors = server.errors

    result.seconds = time.time() - started
    result.passed = result.reason.startswith('completed') and not result.errors and not result.violations
    return result


def standard_scenarios(bot_cls, players=4, ghost_phases=()):
    """The scenario set that found Showbiz's bugs, for any game's bot class."""
    b = lambda n, **k: [(bot_cls, dict(k)) for _ in range(n)]  # noqa: E731
    scenarios = {
        'eager': b(players),
        'solo_min': b(1),
        'idle_mix': b(1, idle=True) + b(players - 1),
        'all_idle': b(players, idle=True),
        'slow': b(players, delay=(0.3, 2.0)),
        'double_tap': b(players, double_tap=0.5),
        'chaos': b(players, chaos=0.3, double_tap=0.3),
        'flaky': b(2, flaky=0.05) + b(players - 2),
        'all_flaky': b(players, flaky=0.08),
        'newgame': b(players),
    }
    for phase_name in ghost_phases:
        scenarios[f'ghost_{phase_name}'] = b(players - 1) + b(1, quit_in=phase_name)
    return scenarios


def run_matrix(make_game, scenarios, seeds=3, verbose=True, **kwargs):
    """Run every scenario with several seeds. Returns True if everything passed.

    Ghost scenarios automatically get a host who presses Skip, and 'newgame' plays twice.
    """
    failures = 0
    for name, bots in scenarios.items():
        if len(bots) < make_game().min_players:
            continue
        for seed in range(seeds):
            opts = dict(kwargs)
            if name.startswith('ghost') or name == 'all_idle':
                opts.setdefault('host_skip_after', 3.0)
            if name == 'newgame':
                opts['games'] = 2
            res = run_scenario(make_game, bots, seed=seed, **opts)
            failures += not res.passed
            if verbose:
                print(f'{name:<22} seed={seed}  {res.summary()}', flush=True)
    if verbose:
        print(f'\n{"ALL PASSED" if not failures else f"{failures} FAILED"}', flush=True)
    return failures == 0


if __name__ == '__main__':  # pragma: no cover
    sys.exit('Import apge.testing from your game\'s test script; see its docstring.')
