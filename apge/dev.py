"""
Developer Mode: the art CMS and a gallery of every screen, for whoever is making the game.

    python app.py --dev          (or APGE_DEV=1) -> opens http://localhost:8080/dev

It only runs from source on the computer running the game: never in a packaged build, and
never for a phone on the Wi-Fi, because it writes files into the game's art/ folder.

Sample screens let Developer Mode show any screen without playing to it. Each one builds a
fresh, throwaway copy of the game (`type(game)()`, so the game class must construct with
no arguments), adds sample players, then calls `setup(game)` to put it in the state to
show. Timers never run in these copies; `elapsed` says how far into the phase the screen
is shown (the timer view and `apge.phaseStarted` agree with it).

    from apge.dev import Screen
    SCREENS = [
        Screen('frisk', 'Random frisk', setup=lambda game: ...),
        Screen('koala', 'Shady Koala (phone)', setup=..., role='player',
               player=lambda game: ...),   # whose phone; default: the first sample player
    ]
    PartyServer(MyGame(), base_dir, art=ART, palette=PALETTE, screens=SCREENS)

Art assets list the screen ids they appear on (Asset.screens) for the live preview.
"""
import base64
import binascii
import io
import json
import os
import secrets
import sys
import traceback

from flask import abort, jsonify, render_template_string, request, send_file

from .art import ArtError
from .game import Player

SAMPLE_NAMES = ['Shazza', 'Davo', 'Kylie', 'Bazza', 'Robbo', 'Jules', 'Macca', 'Tash',
                'Gazza', 'Bec', 'Stevo', 'Lou']
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'dev.html')
LOOPBACK = {'127.0.0.1', '::1', 'localhost'}   # the computer running the game


class Screen:
    """A sample state of one host or phone screen, for previews and debugging."""

    def __init__(self, id, label, setup, role='host', player=None, players=4, elapsed=0.0,
                 group=''):
        if role not in ('host', 'player'):
            raise ValueError(f'Screen {id!r}: role must be "host" or "player"')
        self.id, self.label, self.setup, self.role = id, label, setup, role
        self.player = player          # fn(game) -> the Player whose phone is shown
        self.players = players        # how many sample players
        self.elapsed = float(elapsed)
        self.group = group            # optional heading in the screen gallery

    def to_json(self):
        return {'id': self.id, 'label': self.label, 'role': self.role, 'group': self.group,
                'url': ('/' if self.role == 'host' else '/player') + '?apge-preview=' + self.id}


def dev_requested(argv=None):
    """Was the game started with --dev (or APGE_DEV=1)?"""
    argv = sys.argv[1:] if argv is None else argv
    return '--dev' in argv or os.environ.get('APGE_DEV') == '1'


class DevMode:
    def __init__(self, server, screens, enabled):
        self.server = server
        self.screens = {}
        for screen in screens or ():
            if screen.id in self.screens:
                raise ValueError(f'Screen id {screen.id!r} is listed twice')
            self.screens[screen.id] = screen
        if enabled and getattr(sys, 'frozen', False):
            # A packaged build unpacks into a temp folder: saved art would vanish on exit
            print('Developer Mode only runs from source (python app.py --dev).')
            enabled = False
        self.enabled = bool(enabled)
        self.key = secrets.token_urlsafe(16)

    def allowed(self):
        return self.enabled and request.remote_addr in LOOPBACK

    # ---- sample screens ----------------------------------------------------------

    def build_view(self, screen_id):
        """The view a sample screen's page renders: a fresh game put into that state."""
        screen = self.screens.get(screen_id)
        if not screen:
            raise KeyError(f'No sample screen called {screen_id!r}')
        game = type(self.server.game)()
        game._preview_elapsed = screen.elapsed
        for name in SAMPLE_NAMES[:screen.players]:
            player = Player(name, 'preview')
            game.players[player.id] = player
        screen.setup(game)
        if screen.role == 'host':
            view = dict(game.host_view(), role='display')
        else:
            player = screen.player(game) if screen.player else next(iter(game.players.values()))
            view = game.player_view(player)
        json.dumps(view)   # fail here, with a clear traceback, rather than in the page
        return view

    def preview_config(self, screen_id):
        """APGE_CONFIG.preview for a page opened with ?apge-preview=<screen id>."""
        screen = self.screens.get(screen_id)
        try:
            return {'screen': screen_id, 'elapsed': screen.elapsed if screen else 0,
                    'view': self.build_view(screen_id)}
        except Exception:
            # Shown in the page itself: a broken sample screen is a bug worth seeing
            return {'screen': screen_id, 'error': traceback.format_exc()}

    def manifest(self):
        data = self.server.art.manifest()
        data.update(title=self.server.game.title,
                    screens=[s.to_json() for s in self.screens.values()])
        return data

    # ---- routes ---------------------------------------------------------------------

    def routes(self, app):
        art = self.server.art

        def guard(write=False):
            if not self.allowed():
                abort(404)
            # Writes need the key from the /dev page: another website open in the same
            # browser can't send that header without a CORS preflight, which never succeeds
            if write and (not request.is_json or not secrets.compare_digest(
                    request.headers.get('X-APGE-Dev', ''), self.key)):
                abort(403)

        def refused(error):
            return jsonify({'ok': False, 'error': str(error)}), 400

        @app.route('/dev')
        def dev_page():
            guard()
            with open(TEMPLATE, encoding='utf-8') as f:
                page = f.read()
            return render_template_string(page, title=self.server.game.title, dev_key=self.key)

        @app.route('/apge-dev/manifest')
        def dev_manifest():
            guard()
            return jsonify(self.manifest())

        @app.route('/apge-dev/art/<asset_id>/<version>')
        def dev_version(asset_id, version):
            guard()
            try:
                return jsonify(art.read_version(asset_id, version))
            except ArtError as e:
                return refused(e)

        @app.route('/apge-dev/art/<asset_id>/<version>.png')
        def dev_version_png(asset_id, version):
            guard()
            try:
                png = art.render_png(asset_id, version)
            except ArtError:
                abort(404)
            response = send_file(io.BytesIO(png), mimetype='image/png')
            response.headers['Cache-Control'] = 'no-cache'
            return response

        @app.route('/apge-dev/art/<asset_id>/save', methods=['POST'])
        def dev_save(asset_id):
            guard(write=True)
            data = request.get_json(silent=True) or {}
            try:
                version = art.save_pixels(asset_id, data, version=data.get('version'))
                if data.get('select') is True:
                    art.select(asset_id, version)
            except ArtError as e:
                return refused(e)
            return jsonify({'ok': True, 'version': version})

        @app.route('/apge-dev/art/<asset_id>/import', methods=['POST'])
        def dev_import(asset_id):
            guard(write=True)
            data = request.get_json(silent=True) or {}
            encoded = data.get('png')
            if not isinstance(encoded, str):
                return refused('No picture was uploaded.')
            try:
                png = base64.b64decode(encoded.split(',', 1)[-1], validate=True)
            except (binascii.Error, ValueError):
                return refused("That upload didn't come through - try again.")
            try:
                version = art.import_png(asset_id, png, data.get('label'))
                if data.get('select') is True:
                    art.select(asset_id, version)
            except ArtError as e:
                return refused(e)
            return jsonify({'ok': True, 'version': version})

        @app.route('/apge-dev/art/<asset_id>/select', methods=['POST'])
        def dev_select(asset_id):
            guard(write=True)
            try:
                art.select(asset_id, (request.get_json(silent=True) or {}).get('version'))
            except ArtError as e:
                return refused(e)
            return jsonify({'ok': True})

        @app.route('/apge-dev/art/<asset_id>/<version>/info', methods=['POST'])
        def dev_info(asset_id, version):
            guard(write=True)
            try:
                art.update_info(asset_id, version, request.get_json(silent=True))
            except ArtError as e:
                return refused(e)
            return jsonify({'ok': True})
