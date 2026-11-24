"""
PartyServer: hosts a Game for one host screen and many phones on the local network.

    server = PartyServer(MyGame(), base_dir=os.path.dirname(__file__))
    server.run()

Serves the game's `templates/host.html` at / and `templates/player.html` at /player.
Templates call `{{ apge_head() }}` in <head> to load the APGE client.

Optional: `art` (a list of apge.art.Asset) and `palette` give the game an art library, served
to pages as APGE.art; `screens` (apge.dev.Screen) are sample states for Developer Mode, which
`python app.py --dev` turns on (see apge/dev.py).
"""
import io
import logging
import os
import secrets
import socket
import sys
import threading
import webbrowser

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory
from flask_socketio import SocketIO, join_room, leave_room
from markupsafe import Markup

from . import __version__
from .art import ArtError, ArtLibrary
from .dev import LOOPBACK, DevMode, dev_requested
from .game import LOBBY, Player, Reject

log = logging.getLogger('apge')

APGE_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


def resource_dir(base_dir):
    """Where bundled templates/static live: next to the game, or inside a PyInstaller build."""
    return getattr(sys, '_MEIPASS', base_dir)


def get_local_ip():
    """LAN address phones should use. UDP connect sends nothing; it just picks a route."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'localhost'


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


class PartyServer:
    def __init__(self, game, base_dir, port=None, host_template='host.html',
                 player_template='player.html', art=None, palette=None, screens=None, dev=None):
        self.game = game
        game._server = self
        root = resource_dir(base_dir)
        self.port = int(port or os.environ.get('APGE_PORT', 8080))
        self.host_key = secrets.token_urlsafe(16)
        self.host_template = host_template
        self.player_template = player_template
        self.art = ArtLibrary(os.path.join(root, 'art'), art or (), palette)
        self.dev = DevMode(self, screens, dev_requested() if dev is None else dev)

        self.app = Flask(game.__class__.__module__,
                         template_folder=os.path.join(root, 'templates'),
                         static_folder=os.path.join(root, 'static'))
        self.socketio = SocketIO(self.app, cors_allowed_origins='*', async_mode='threading')

        # sid -> {'role': 'guest'|'player'|'host'|'display', 'player_id': str|None}
        self.conns = {}
        self.player_sid = {}   # player_id -> current sid

        self._routes()
        self.dev.routes(self.app)
        self._events()

    # ---- HTTP -------------------------------------------------------------

    def _routes(self):
        app = self.app

        @app.context_processor
        def inject():
            return {'apge_head': self._head_markup}

        @app.route('/')
        def host_page():
            return render_template(self.host_template, title=self.game.title)

        @app.route('/player')
        def player_page():
            return render_template(self.player_template, title=self.game.title)

        @app.route('/apge/<path:filename>')
        def apge_static(filename):
            return send_from_directory(APGE_STATIC, filename)

        @app.route('/apge-qr.png')
        def qr():
            import qrcode
            img = qrcode.make(self.player_url(), box_size=10, border=4)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            return send_file(buf, mimetype='image/png')

        @app.route('/apge-art/<asset_id>.png')
        def art_png(asset_id):
            try:
                png = self.art.render_png(asset_id)
            except ArtError:
                abort(404)
            response = send_file(io.BytesIO(png), mimetype='image/png')
            response.headers['Cache-Control'] = 'no-cache'
            return response

        @app.route('/apge-info')
        def info():
            return jsonify({'title': self.game.title, 'player_url': self.player_url(),
                            'apge_version': __version__})

    def _head_markup(self):
        """<head> tags for an APGE page: styles, socket.io, the client and its config."""
        is_host_page = request.path == '/'
        trusted = request.remote_addr in LOOPBACK or os.environ.get('APGE_REMOTE_HOST') == '1'
        config = {
            'role': 'host' if is_host_page else 'player',
            'apgeVersion': __version__,
            # Only the machine running the game gets host controls; a phone that opens /
            # sees the host screen read-only
            'hostKey': self.host_key if (is_host_page and trusted) else None,
            'art': self.art.client_config(),
        }
        preview = request.args.get('apge-preview')
        if preview and self.dev.allowed():
            # A Developer Mode sample screen: no socket, a made-up state, no host controls
            config['preview'] = self.dev.preview_config(preview)
            config['hostKey'] = None
        import json
        return Markup(
            '<link rel="stylesheet" href="/apge/apge.css">\n'
            '<script src="/apge/socket.io.min.js"></script>\n'
            f'<script>window.APGE_CONFIG = {json.dumps(config)};</script>\n'
            '<script src="/apge/apge.js"></script>'
        )

    def player_url(self):
        return f'http://{get_local_ip()}:{self.port}/player'

    # ---- Socket events ----------------------------------------------------

    def _locked(self, fn):
        """Run an entry point under the game lock, then push views once."""
        game = self.game

        def wrapper(*args):
            with game.lock:
                try:
                    return fn(*args)
                except Reject as r:
                    return {'ok': False, 'error': str(r)}
                except Exception:
                    log.exception('error handling %s', fn.__name__)
                    game.mark_dirty()
                    return {'ok': False, 'error': 'Something went wrong on the server.'}
                finally:
                    game._flush()
        wrapper.__name__ = fn.__name__
        return wrapper

    def _events(self):
        sio, game = self.socketio, self.game
        on = lambda name: (lambda fn: sio.on(name)(self._locked(fn)))  # noqa: E731

        @on('connect')
        def connect(auth=None):
            self.conns[request.sid] = {'role': 'guest', 'player_id': None}
            # A page that just (re)loaded gets the current state straight away
            sio.emit('apge:state', game.public_view(), to=request.sid)

        @on('disconnect')
        def disconnect(reason=None):
            conn = self.conns.pop(request.sid, None)
            if not conn or not conn['player_id']:
                return
            pid = conn['player_id']
            if self.player_sid.get(pid) == request.sid:
                del self.player_sid[pid]
                player = game.players.get(pid)
                if player:
                    player.connected = False
                    log.info('%s disconnected', player.name)
                    game.on_player_disconnected(player)
                    game.mark_dirty()
                    # A Continue screen may now be complete without them
                    game._check_ready_complete()

        @on('apge:host')
        def host_hello(data=None):
            key = (data or {}).get('key')
            role = 'host' if key and secrets.compare_digest(str(key), self.host_key) else 'display'
            self.conns[request.sid] = {'role': role, 'player_id': None}
            join_room('screens')
            game.mark_dirty()
            return {'ok': True, 'role': role}

        @on('apge:join')
        def join(data=None):
            data = data or {}
            name = ' '.join(str(data.get('name', '')).split())[:game.max_name]
            token = str(data.get('token') or '')
            if not name:
                raise Reject('Please enter a name.')

            player = game.player_by_name(name)
            if player:
                old_sid = self.player_sid.get(player.id)
                if old_sid == request.sid:
                    return {'ok': True, 'player': {'id': player.id, 'name': player.name}}
                if old_sid and token != player.token:
                    raise Reject(f'"{name}" is already taken - pick another name.')
                # Reconnect (same browser, or a new phone after the old one went away)
                if old_sid:
                    # Same browser, second tab: the older tab stops being this player
                    self.conns[old_sid] = {'role': 'guest', 'player_id': None}
                    sio.emit('apge:replaced', {}, to=old_sid)
                player.token = token or player.token
                player.connected = True
                self._bind(player)
                log.info('%s reconnected', name)
                game.on_player_reconnected(player)
            else:
                if game.phase != LOBBY and not game.allow_late_join:
                    raise Reject('This game is already in progress - wait for the next one!')
                if len(game.players) >= game.max_players:
                    raise Reject(f'The game is full ({game.max_players} players).')
                player = Player(name, token)
                game.players[player.id] = player
                self._bind(player)
                log.info('%s joined', name)
                game.on_player_joined(player)
            game.mark_dirty()
            return {'ok': True, 'player': {'id': player.id, 'name': player.name}}

        @on('apge:action')
        def player_action(data=None):
            data = data or {}
            player = self._player_for(request.sid)
            result = game._dispatch(player, str(data.get('name', '')), data.get('data'))
            return {'ok': True, 'result': result}

        @on('apge:ready')
        def ready(data=None):
            game._set_ready(self._player_for(request.sid))
            return {'ok': True}

        @on('apge:host_command')
        def host_command(data=None):
            data = data or {}
            command = data.get('command')
            if not self._may_command(request.sid, command):
                raise Reject('Only the host computer can do that.')
            if command == 'start':
                game._start()
            elif command == 'skip':
                if game.phase in (LOBBY, game.end_phase):
                    raise Reject('Nothing to skip.')
                game._skip()
            elif command == 'reset':
                game._reset()
            elif command == 'quit':
                for player_id in game._quit():
                    self._unbind(player_id, 'quit')
            elif command == 'kick':
                self._kick(data.get('player_id'))
            else:
                raise Reject(f'Unknown host command {command!r}.')
            return {'ok': True}

        # Relay: low-latency player input straight to the screens (no lock, no state).
        # For realtime "phone as controller" games where the host page runs the action.
        @sio.on('apge:relay')
        def relay(data=None):
            conn = self.conns.get(request.sid)
            if not conn or not conn['player_id'] or not isinstance(data, dict):
                return
            sio.emit('apge:relay', {'player_id': conn['player_id'], 'event': data.get('event'),
                                    'data': data.get('data')}, to='screens')

    def _bind(self, player):
        old = self.conns.get(request.sid)
        if old and old['role'] in ('host', 'display'):
            leave_room('screens')
        self.conns[request.sid] = {'role': 'player', 'player_id': player.id}
        self.player_sid[player.id] = request.sid

    def _may_command(self, sid, command):
        """Host computer: any command. The VIP's phone: only the game's vip_commands."""
        conn = self.conns.get(sid, {})
        if conn.get('role') == 'host':
            return True
        if conn.get('role') == 'player' and command in self.game.vip_commands:
            return conn['player_id'] is not None and conn['player_id'] == self.game._vip_id()
        return False

    def _unbind(self, player_id, reason):
        """Detach a removed player's phone; it goes back to the join screen."""
        sid = self.player_sid.pop(player_id, None)
        if sid:
            self.conns[sid] = {'role': 'guest', 'player_id': None}
            self.socketio.emit('apge:kicked', {'reason': reason}, to=sid)

    def _player_for(self, sid):
        conn = self.conns.get(sid)
        player = conn and conn['player_id'] and self.game.players.get(conn['player_id'])
        if not player:
            raise Reject('You are not in this game - join first.')
        return player

    def _kick(self, player_id):
        game = self.game
        if game.phase != LOBBY:
            raise Reject('Players can only be removed in the lobby.')
        player = game.players.pop(player_id, None)
        if not player:
            raise Reject('No such player.')
        self._unbind(player_id, 'kick')
        game.mark_dirty()

    # ---- pushing state ----------------------------------------------------

    def push_views(self):
        """Send every connection its own view. Called with the game lock held."""
        game = self.game
        screens = None
        public = None
        for sid, conn in list(self.conns.items()):
            role = conn['role']
            if role == 'player':
                player = game.players.get(conn['player_id'])
                view = game.player_view(player) if player else game.public_view()
            elif role in ('host', 'display'):
                if screens is None:
                    screens = game.host_view()
                view = dict(screens, role=role)
            else:
                if public is None:
                    public = game.public_view()
                view = public
            self.socketio.emit('apge:state', view, to=sid)

    def send_to_player(self, player, event, data=None):
        sid = self.player_sid.get(player.id)
        if sid:
            self.socketio.emit('apge:message', {'event': event, 'data': data}, to=sid)

    def send_to_all(self, event, data=None):
        self.socketio.emit('apge:message', {'event': event, 'data': data})

    # ---- running ----------------------------------------------------------

    def run(self, open_browser=None):
        """Start serving. Blocks until the process is stopped."""
        # Windows consoles can choke on emoji in game logs
        for stream in (sys.stdout, sys.stderr):
            if stream and hasattr(stream, 'reconfigure'):
                stream.reconfigure(errors='replace')
        logging.basicConfig(level=logging.INFO, format='%(message)s')
        logging.getLogger('werkzeug').setLevel(logging.WARNING)

        if port_in_use(self.port):
            print(f'\nPort {self.port} is already in use - is {self.game.title} already running?')
            print(f'Open http://localhost:{self.port} in your browser, '
                  f'or set APGE_PORT to use another port.')
            if getattr(sys, 'frozen', False):
                input('\nPress Enter to exit...')
            sys.exit(1)

        if open_browser is None:
            open_browser = not os.environ.get('APGE_NO_BROWSER')

        print('\n' + '=' * 50)
        print(f'  {self.game.title}')
        print('=' * 50)
        print(f'\n  Host screen:  http://localhost:{self.port}')
        print(f'  Players join: {self.player_url()}')
        if self.dev.enabled:
            print(f'  Developer Mode: http://localhost:{self.port}/dev')
        print('\n  Keep this window open while you play.')
        print('  Close it (or press Ctrl+C) to stop the game.')
        print('=' * 50 + '\n')

        if open_browser:
            page = '/dev' if self.dev.enabled else ''
            threading.Timer(1.5, webbrowser.open, args=[f'http://localhost:{self.port}{page}']).start()

        # No debug reloader: it runs the script twice and can't work in a packaged build
        self.socketio.run(self.app, host='0.0.0.0', port=self.port, debug=False,
                          use_reloader=False, allow_unsafe_werkzeug=True, log_output=False)
