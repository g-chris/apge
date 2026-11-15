"""
Engine tests: protocol behaviour learned the hard way in Showbiz, plus checks that the
bot harness really does catch broken games.

    python -m unittest discover tests        (needs: pip install -e ".[testing]")
"""
import os
import sys
import tempfile
import time
import unittest

import socketio

from apge import Game, Reject, action, on_all_ready, on_timeout, phase
from apge.__main__ import new_game
from apge.testing import Bot, TestServer, run_scenario

SCAFFOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'apge', 'scaffold')
sys.path[:0] = [SCAFFOLD, os.path.join(SCAFFOLD, 'tests')]
from game import HotTake  # noqa: E402
from test_bots import HotTakeBot  # noqa: E402  (the scaffold's own bot)


class Counter(Game):
    """Tiny game: everyone taps once, then a Continue screen, then game over."""
    title = 'Counter'
    min_players = 1

    def on_start(self):
        self.public['taps'] = 0
        self.goto('tap')

    @phase('tap', timer=30)
    def enter_tap(self):
        for p in self.players.values():
            p.private['tapped'] = False

    @action('tap', phases='tap')
    def tap(self, player, data):
        if player.private['tapped']:
            raise Reject('Already tapped')
        player.private['tapped'] = True
        self.public['taps'] += 1
        if self.everyone(lambda p: p.private['tapped']):
            self.goto('results')

    @on_timeout('tap')
    def tap_timeout(self):
        self.goto('results')

    @phase('results', timer=30, ready=True)
    def enter_results(self):
        pass

    @on_all_ready('results')
    def results_done(self):
        self.goto('game_over')


def client(server, name=None, token=None):
    c = socketio.Client(reconnection=False)
    c.views = []
    c.on('apge:state', lambda v: c.views.append(v))
    c.connect(server.url, transports=['websocket'])
    if name:
        c.ack = c.call('apge:join', {'name': name, 'token': token or name + '-token'}, timeout=5)
    return c


def settle():
    time.sleep(0.3)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.game = Counter()
        self.server = TestServer(self.game, time_scale=1.0).__enter__()
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            try:
                c.disconnect()
            except Exception:
                pass
        self.server.__exit__(None, None, None)

    def join(self, name, token=None):
        c = client(self.server, name, token)
        self.clients.append(c)
        return c

    def host(self):
        h = self.server.host_client()
        self.clients.append(h)
        return h

    def test_reconnect_keeps_player_id_and_data(self):
        a = self.join('Ann')
        pid = a.ack['player']['id']
        self.host().call('apge:host_command', {'command': 'start'})
        a.call('apge:action', {'name': 'tap', 'data': {}})
        a.disconnect()
        settle()
        self.assertFalse(self.game.players[pid].connected)
        a2 = self.join('Ann')  # same token -> same player
        self.assertEqual(a2.ack['player']['id'], pid)
        self.assertTrue(self.game.players[pid].connected)
        settle()
        self.assertEqual(a2.views[-1]['me']['id'], pid)

    def test_name_taken_by_someone_else_is_rejected(self):
        self.join('Ann', token='phone-1')
        b = self.join('Ann', token='phone-2')
        self.assertFalse(b.ack['ok'])
        self.assertIn('taken', b.ack['error'])
        self.assertEqual(len(self.game.players), 1)

    def test_lost_phone_can_rejoin_from_another_device(self):
        a = self.join('Ann', token='phone-1')
        a.disconnect()
        settle()
        b = self.join('Ann', token='phone-2')
        self.assertTrue(b.ack['ok'])

    def test_second_tab_replaces_first(self):
        first = self.join('Ann')
        replaced = []
        first.on('apge:replaced', lambda d: replaced.append(1))
        second = self.join('Ann')
        settle()
        self.assertTrue(second.ack['ok'])
        self.assertEqual(replaced, [1])
        ack = first.call('apge:action', {'name': 'tap', 'data': {}})
        self.assertFalse(ack['ok'])

    def test_late_join_rejected_after_start(self):
        self.join('Ann')
        self.host().call('apge:host_command', {'command': 'start'})
        late = self.join('Bob')
        self.assertFalse(late.ack['ok'])
        self.assertIn('in progress', late.ack['error'])

    def test_action_outside_its_phase_is_rejected(self):
        a = self.join('Ann')
        b = self.join('Bob')
        self.host().call('apge:host_command', {'command': 'start'})
        a.call('apge:action', {'name': 'tap', 'data': {}})
        b.call('apge:action', {'name': 'tap', 'data': {}})
        self.assertEqual(self.game.phase, 'results')
        late = a.call('apge:action', {'name': 'tap', 'data': {}})
        self.assertFalse(late['ok'])
        self.assertEqual(self.game.public['taps'], 2)

    def test_host_commands_need_host_key(self):
        a = self.join('Ann')
        self.assertFalse(a.call('apge:host_command', {'command': 'start'})['ok'])
        display = client(self.server)
        self.clients.append(display)
        role = display.call('apge:host', {'key': 'wrong'})['role']
        self.assertEqual(role, 'display')
        self.assertFalse(display.call('apge:host_command', {'command': 'start'})['ok'])
        self.assertEqual(self.game.phase, 'lobby')

    def test_disconnected_player_does_not_block_continue(self):
        a = self.join('Ann')
        b = self.join('Bob')
        self.host().call('apge:host_command', {'command': 'start'})
        a.call('apge:action', {'name': 'tap', 'data': {}})
        b.call('apge:action', {'name': 'tap', 'data': {}})
        a.call('apge:ready', {})
        self.assertEqual(self.game.phase, 'results')
        b.disconnect()
        settle()
        self.assertEqual(self.game.phase, 'game_over')

    def test_skip_acts_like_timeout(self):
        self.join('Ann')
        self.join('Bob')
        h = self.host()
        h.call('apge:host_command', {'command': 'start'})
        h.call('apge:host_command', {'command': 'skip'})
        self.assertEqual(self.game.phase, 'results')
        h.call('apge:host_command', {'command': 'skip'})
        self.assertEqual(self.game.phase, 'game_over')

    def test_reset_keeps_roster(self):
        a = self.join('Ann')
        h = self.host()
        h.call('apge:host_command', {'command': 'start'})
        h.call('apge:host_command', {'command': 'reset'})
        self.assertEqual(self.game.phase, 'lobby')
        self.assertEqual([p.name for p in self.game.players.values()], ['Ann'])
        self.assertTrue(h.call('apge:host_command', {'command': 'start'})['ok'])

    def test_private_data_only_reaches_its_owner(self):
        a = self.join('Ann')
        b = self.join('Bob')
        self.host().call('apge:host_command', {'command': 'start'})
        a.call('apge:action', {'name': 'tap', 'data': {}})
        settle()
        self.assertTrue(a.views[-1]['me']['private']['tapped'])
        self.assertFalse(b.views[-1]['me']['private']['tapped'])
        for p in b.views[-1]['players']:
            self.assertNotIn('private', p)

    def test_stale_timer_is_ignored(self):
        self.game.time_scale = 0.01   # tap timer: 0.3s
        a = self.join('Ann')
        self.host().call('apge:host_command', {'command': 'start'})
        a.call('apge:action', {'name': 'tap', 'data': {}})   # -> results (new 0.3s timer)
        a.call('apge:ready', {})                               # -> game_over
        time.sleep(0.6)                                        # old timers fire now
        self.assertEqual(self.game.phase, 'game_over')
        self.assertEqual(self.server.errors, [])

    def test_kick_in_lobby(self):
        self.join('Ann')
        b = self.join('Bob')
        h = self.host()
        h.call('apge:host_command', {'command': 'kick', 'player_id': b.ack['player']['id']})
        self.assertEqual([p.name for p in self.game.players.values()], ['Ann'])

    def test_name_length_comes_from_the_game(self):
        self.game.max_name = 5
        a = self.join('Annabelle')
        self.assertEqual(a.ack['player']['name'], 'Annab')
        settle()
        self.assertEqual(a.views[-1]['max_name'], 5)

    def test_vip_phone_may_send_only_vip_commands(self):
        self.game.vip_commands = ('start',)
        a = self.join('Ann')
        b = self.join('Bob')
        settle()
        self.assertEqual(b.views[-1]['vip_id'], a.ack['player']['id'])
        self.assertFalse(b.call('apge:host_command', {'command': 'start'})['ok'])
        self.assertEqual(self.game.phase, 'lobby')
        self.assertTrue(a.call('apge:host_command', {'command': 'start'})['ok'])
        self.assertEqual(self.game.phase, 'tap')
        # Skip isn't a VIP command: a phone must not be able to rush everyone else
        self.assertFalse(a.call('apge:host_command', {'command': 'skip'})['ok'])
        self.assertEqual(self.game.phase, 'tap')

    def test_vip_passes_on_when_vip_disconnects(self):
        self.game.vip_commands = ('start',)
        a = self.join('Ann')
        b = self.join('Bob')
        a.disconnect()
        settle()
        self.assertEqual(b.views[-1]['vip_id'], b.ack['player']['id'])
        self.assertTrue(b.call('apge:host_command', {'command': 'start'})['ok'])

    def test_no_vip_unless_the_game_opts_in(self):
        a = self.join('Ann')
        settle()
        self.assertIsNone(a.views[-1]['vip_id'])
        self.assertFalse(a.call('apge:host_command', {'command': 'start'})['ok'])

    def test_quit_removes_every_player(self):
        a = self.join('Ann')
        self.join('Bob')
        kicked = []
        a.on('apge:kicked', lambda d: kicked.append(d))
        h = self.host()
        h.call('apge:host_command', {'command': 'start'})
        self.assertTrue(h.call('apge:host_command', {'command': 'quit'})['ok'])
        settle()
        self.assertEqual(self.game.phase, 'lobby')
        self.assertEqual(self.game.players, {})
        self.assertEqual(kicked, [{'reason': 'quit'}])
        self.assertFalse(a.call('apge:action', {'name': 'tap', 'data': {}})['ok'])
        # Same phone can join the next session
        self.assertTrue(a.call('apge:join', {'name': 'Ann', 'token': 'Ann-token'})['ok'])

    def test_lobby_ready_is_reported(self):
        a = self.join('Ann')
        settle()
        self.assertTrue(a.views[-1]['lobby_ready'])
        self.game.lobby_ready = lambda: False
        self.game.mark_dirty()
        self.join('Bob')
        settle()
        self.assertFalse(a.views[-1]['lobby_ready'])


class ScaffoldTests(unittest.TestCase):
    def test_new_game_into_fresh_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, '.git'))
            new_game(tmp, 'Fresh Repo')
            self.assertTrue(os.path.exists(os.path.join(tmp, 'game.py')))

    def test_slug_ignores_apostrophes(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_game(tmp, "Gambler's Derby '27")
            with open(os.path.join(tmp, 'build.spec')) as f:
                self.assertIn("name='GamblersDerby27'", f.read())

    def test_new_game_refuses_a_dir_with_work_in_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'notes.txt'), 'w').close()
            with self.assertRaises(SystemExit):
                new_game(tmp, 'Clash')


# ---- the harness must catch broken games --------------------------------------------

class NoTimeoutHotTake(HotTake):
    """Bug: forgot the write timeout, so an idle player blocks the round forever."""
    @on_timeout('write')
    def write_timeout(self):
        pass


class CrashingHotTake(HotTake):
    @action('vote', phases='vote')
    def vote(self, player, data):
        raise KeyError('oops')


class SetupCounter(Counter):
    """Players must 'setup' (think: draw an avatar) in the lobby before the game is ready."""

    def lobby_ready(self):
        return bool(self.players) and all(p.private.get('setup') for p in self.players.values())

    @action('setup', phases='lobby')
    def setup(self, player, data):
        player.private['setup'] = True

    def on_start(self):
        self.public['set_up_at_start'] = sum(bool(p.private.get('setup')) for p in self.players.values())
        super().on_start()


class SetupBot(Bot):
    def decide(self, view):
        if view['phase'] == 'lobby' and not view['me']['private'].get('setup'):
            return [('setup', {})]
        if view['phase'] == 'tap' and not view['me']['private'].get('tapped'):
            return [('tap', {})]


class HarnessSelfTests(unittest.TestCase):
    bots = [(HotTakeBot, {}), (HotTakeBot, {})]

    def test_detects_stall(self):
        res = run_scenario(NoTimeoutHotTake, [(HotTakeBot, {}), (HotTakeBot, {'idle': True})], stall=3)
        self.assertFalse(res.passed)
        self.assertIn('stalled in write', res.reason)

    def test_detects_server_errors(self):
        res = run_scenario(CrashingHotTake, self.bots, stall=5)
        self.assertFalse(res.passed)
        self.assertTrue(any('oops' in e for e in res.errors))

    def test_detects_invariant_failure(self):
        def invariant(game):
            assert game.phase != 'reveal', 'reached reveal'
        res = run_scenario(HotTake, self.bots, invariant=invariant, stall=5)
        self.assertFalse(res.passed)
        self.assertTrue(any('reached reveal' in v for v in res.violations))

    def test_waits_for_lobby_setup_before_start(self):
        games = []

        def make():
            games.append(SetupCounter())
            return games[-1]
        bots = [(SetupBot, {'delay': (0.5, 1.0)}) for _ in range(3)]
        res = run_scenario(make, bots, stall=5)
        self.assertTrue(res.passed, res.summary())
        self.assertEqual(games[-1].public['set_up_at_start'], 3)

    def test_starts_anyway_when_lobby_setup_never_finishes(self):
        res = run_scenario(SetupCounter, [(SetupBot, {'idle': True})], stall=5, start_wait=0.5,
                           host_skip_after=1.0)
        self.assertTrue(res.passed, res.summary())

    def test_healthy_game_passes(self):
        res = run_scenario(HotTake, self.bots + [(HotTakeBot, {'flaky': 0.05})])
        self.assertTrue(res.passed, res.summary())


if __name__ == '__main__':
    unittest.main()
