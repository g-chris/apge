"""
Art library and Developer Mode tests: what gets saved, what gets refused, who may use it.

    python -m unittest discover tests
"""
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image

from apge import Game, PartyServer, phase
from apge.art import ArtError, ArtLibrary, Asset
from apge.dev import Screen, dev_requested

PALETTE = ['#000000', '#ffffff', '#ff0000']
LAN = {'REMOTE_ADDR': '192.168.1.20'}


class Tiny(Game):
    title = 'Tiny'

    def on_start(self):
        self.goto('wait')

    @phase('wait', timer=30)
    def enter_wait(self):
        self.public['waiting'] = True


def png_bytes(size=(4, 3), colour=(255, 0, 0, 255)):
    buf = io.BytesIO()
    Image.new('RGBA', size, colour).save(buf, format='PNG')
    return buf.getvalue()


def pixels(width, height, char='.'):
    return char * (width * height)


class ArtLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, 'art')
        self.lib = ArtLibrary(self.root, [Asset('agent', 'Agent', 'People', (3, 2), screens=['frisk'])], PALETTE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_versions_number_up_and_render_one_pixel_per_cell(self):
        self.assertEqual(self.lib.save_pixels('agent', {'width': 3, 'height': 2, 'frames': ['012...']}), 'v1')
        self.assertEqual(self.lib.save_pixels('agent', {'width': 3, 'height': 2, 'frames': ['......']}), 'v2')
        img = Image.open(io.BytesIO(self.lib.render_png('agent', 'v1')))
        self.assertEqual(img.size, (3, 2))
        self.assertEqual(img.getpixel((2, 0)), (255, 0, 0, 255))
        self.assertEqual(img.getpixel((0, 1))[3], 0)   # '.' is transparent

    def test_rows_are_stored_one_per_line_for_readable_diffs(self):
        self.lib.save_pixels('agent', {'width': 3, 'height': 2, 'frames': ['012...']})
        with open(os.path.join(self.root, 'agent', 'v1.json')) as f:
            self.assertEqual(json.load(f)['frames'], [['012', '...']])

    def test_nothing_in_game_until_a_version_is_selected(self):
        self.lib.save_pixels('agent', {'width': 3, 'height': 2, 'frames': [pixels(3, 2)]})
        self.assertIsNone(self.lib.client_config()['agent']['url'])
        with self.assertRaises(ArtError):
            self.lib.render_png('agent')
        self.lib.select('agent', 'v1')
        config = self.lib.client_config()['agent']
        self.assertTrue(config['url'].startswith('/apge-art/agent.png?v=v1.'))
        self.assertEqual((config['w'], config['h'], config['pixelated']), (3, 2, True))

    def test_overwrite_keeps_created_date_and_label_and_may_change_size(self):
        v = self.lib.save_pixels('agent', {'width': 3, 'height': 2, 'frames': [pixels(3, 2)], 'label': 'first'})
        created = self.lib.read_version('agent', v)['created']
        self.lib.save_pixels('agent', {'width': 4, 'height': 4, 'frames': [pixels(4, 4, '1')]}, version=v)
        data = self.lib.read_version('agent', v)
        self.assertEqual((data['width'], data['label'], data['created']), (4, 'first', created))

    def test_refuses_bad_pixel_saves(self):
        bad = [
            {'width': True, 'height': 2, 'frames': [pixels(1, 2)]},       # bool is not a size
            {'width': 3, 'height': 2, 'frames': ['01']},                   # wrong length
            {'width': 3, 'height': 2, 'frames': ['0129..']},               # '9' is not in a 3-colour palette
            {'width': 3, 'height': 2, 'frames': []},
            {'width': 3, 'height': 2, 'frames': [pixels(3, 2)] * 65},
            {'width': 300, 'height': 2, 'frames': [pixels(300, 2)]},
            {'width': 3, 'height': 2, 'frames': [123456]},
            'not a dict',
        ]
        for data in bad:
            with self.assertRaises(ArtError, msg=repr(data)[:80]):
                self.lib.save_pixels('agent', data)
        self.assertEqual(self.lib.version_ids('agent'), [])

    def test_unknown_assets_and_versions_never_touch_the_disk(self):
        good = {'width': 3, 'height': 2, 'frames': [pixels(3, 2)]}
        for asset_id in ('nope', '../agent', None, 5):
            with self.assertRaises(ArtError):
                self.lib.save_pixels(asset_id, good)
        for version in ('../../x', 'v0', 'v1/../../x', 'x', 7):
            with self.assertRaises(ArtError):
                self.lib.save_pixels('agent', good, version=version)
        for version in ('../../x', 'v1', None):
            with self.assertRaises(ArtError):
                self.lib.select('agent', version)
        self.assertFalse(os.path.exists(self.root))

    def test_png_import_is_reencoded_and_cannot_be_overwritten(self):
        v = self.lib.import_png('agent', png_bytes(), label='scan')
        data = self.lib.read_version('agent', v)
        self.assertEqual((data['kind'], data['width'], data['height'], data['pixelated']), ('png', 4, 3, True))
        self.assertEqual(Image.open(io.BytesIO(self.lib.render_png('agent', v))).size, (4, 3))
        with self.assertRaises(ArtError):
            self.lib.save_pixels('agent', {'width': 3, 'height': 2, 'frames': [pixels(3, 2)]}, version=v)

    def test_png_import_refuses_non_pictures_and_huge_pictures(self):
        with self.assertRaises(ArtError):
            self.lib.import_png('agent', b'definitely not a png')
        with self.assertRaises(ArtError):
            self.lib.import_png('agent', png_bytes((2049, 1)))
        self.assertEqual(self.lib.version_ids('agent'), [])

    def test_large_imports_default_to_smooth_scaling(self):
        v = self.lib.import_png('agent', png_bytes((600, 400)))
        self.assertFalse(self.lib.read_version('agent', v)['pixelated'])
        self.lib.update_info('agent', v, {'pixelated': True, 'label': '  painted   one '})
        data = self.lib.read_version('agent', v)
        self.assertEqual((data['pixelated'], data['label']), (True, 'painted one'))
        with self.assertRaises(ArtError):
            self.lib.update_info('agent', v, {'pixelated': 'yes'})

    def test_a_broken_version_file_is_skipped_not_fatal(self):
        self.lib.save_pixels('agent', {'width': 3, 'height': 2, 'frames': [pixels(3, 2)]})
        with open(os.path.join(self.root, 'agent', 'v2.json'), 'w') as f:
            f.write('{half a fi')
        versions = self.lib.manifest()['assets'][0]['versions']
        self.assertEqual([v['id'] for v in versions], ['v1'])
        self.assertNotIn('frames', versions[0])

    def test_asset_list_is_checked(self):
        with self.assertRaises(ValueError):
            Asset('Bad Id', 'x', 'c', (1, 1))
        with self.assertRaises(ValueError):
            Asset('big', 'x', 'c', (1000, 1))
        with self.assertRaises(ValueError):
            ArtLibrary(self.root, [Asset('a', 'x', 'c', (1, 1)), Asset('a', 'y', 'c', (1, 1))], PALETTE)


class DevModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, 'templates'))
        for name in ('host.html', 'player.html'):
            with open(os.path.join(self.tmp.name, 'templates', name), 'w') as f:
                f.write('<html><head>{{ apge_head() }}</head><body></body></html>')
        self.screens = [
            Screen('waiting', 'Waiting', setup=lambda g: g._start()),
            Screen('phone', 'Phone', setup=lambda g: g._start(), role='player',
                   player=lambda g: list(g.players.values())[1], elapsed=12),
            Screen('broken', 'Broken', setup=lambda g: 1 / 0),
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def server(self, dev=True):
        art = [Asset('agent', 'Agent', 'People', (3, 2), screens=['waiting'])]
        server = PartyServer(Tiny(), base_dir=self.tmp.name, art=art, palette=PALETTE,
                             screens=self.screens, dev=dev)
        return server, server.app.test_client()

    def save(self, client, server, body, key=True):
        headers = {'X-APGE-Dev': server.dev.key} if key else {}
        return client.post('/apge-dev/art/agent/save', json=body, headers=headers)

    def config(self, html):
        return json.loads(re.search(r'window\.APGE_CONFIG = (.*?);</script>', html).group(1))

    def test_dev_pages_only_exist_in_dev_mode_on_the_host_computer(self):
        _, off = self.server(dev=False)
        self.assertEqual(off.get('/dev').status_code, 404)
        self.assertEqual(off.get('/apge-dev/manifest').status_code, 404)
        server, client = self.server()
        self.assertEqual(client.get('/dev').status_code, 200)
        self.assertEqual(client.get('/dev', environ_base=LAN).status_code, 404)
        self.assertEqual(client.post('/apge-dev/art/agent/save', json={}, environ_base=LAN,
                                     headers={'X-APGE-Dev': server.dev.key}).status_code, 404)

    def test_writes_need_the_dev_key_and_json(self):
        server, client = self.server()
        body = {'width': 3, 'height': 2, 'frames': [pixels(3, 2)]}
        self.assertEqual(self.save(client, server, body, key=False).status_code, 403)
        self.assertEqual(client.post('/apge-dev/art/agent/save', data=json.dumps(body),
                                     headers={'X-APGE-Dev': server.dev.key, 'Content-Type': 'text/plain'}).status_code, 403)
        self.assertEqual(server.art.version_ids('agent'), [])

    def test_save_select_and_serve(self):
        server, client = self.server()
        self.assertEqual(client.get('/apge-art/agent.png').status_code, 404)
        r = self.save(client, server, {'width': 3, 'height': 2, 'frames': ['012...'], 'select': True})
        self.assertEqual(r.get_json(), {'ok': True, 'version': 'v1'})
        png = client.get('/apge-art/agent.png')
        self.assertEqual((png.status_code, png.mimetype), (200, 'image/png'))
        # Saved art reaches every page, phones included
        self.assertTrue(self.config(client.get('/player', environ_base=LAN).get_data(as_text=True))['art']['agent']['url'])
        manifest = client.get('/apge-dev/manifest').get_json()
        self.assertEqual(manifest['assets'][0]['selected'], 'v1')
        self.assertEqual([s['id'] for s in manifest['screens']], ['waiting', 'phone', 'broken'])

    def test_refusals_come_back_as_messages(self):
        server, client = self.server()
        r = self.save(client, server, {'width': 3, 'height': 2, 'frames': ['01']})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()['ok'])
        r = client.post('/apge-dev/art/agent/import', json={'png': 'not base64!!'},
                        headers={'X-APGE-Dev': server.dev.key})
        self.assertEqual(r.status_code, 400)

    def test_png_import_over_http(self):
        import base64
        server, client = self.server()
        data = 'data:image/png;base64,' + base64.b64encode(png_bytes()).decode()
        r = client.post('/apge-dev/art/agent/import', json={'png': data, 'label': 'scan'},
                        headers={'X-APGE-Dev': server.dev.key})
        self.assertEqual(r.get_json(), {'ok': True, 'version': 'v1'})
        self.assertEqual(client.get('/apge-dev/art/agent/v1.png').status_code, 200)

    def test_preview_pages_render_a_sample_state_without_host_controls(self):
        _, client = self.server()
        config = self.config(client.get('/?apge-preview=waiting').get_data(as_text=True))
        self.assertIsNone(config['hostKey'])
        view = config['preview']['view']
        self.assertEqual((view['phase'], view['role'], len(view['players'])), ('wait', 'display', 4))
        phone = self.config(client.get('/player?apge-preview=phone').get_data(as_text=True))['preview']
        self.assertEqual(phone['view']['me']['name'], 'Davo')
        self.assertEqual(phone['view']['timer']['remaining'], 18.0)   # 30s timer, shown 12s in

    def test_a_broken_sample_screen_shows_its_error(self):
        _, client = self.server()
        preview = self.config(client.get('/?apge-preview=broken').get_data(as_text=True))['preview']
        self.assertIn('ZeroDivisionError', preview['error'])

    def test_preview_is_ignored_outside_dev_mode(self):
        _, off = self.server(dev=False)
        self.assertNotIn('preview', self.config(off.get('/?apge-preview=waiting').get_data(as_text=True)))
        _, client = self.server()
        self.assertNotIn('preview', self.config(client.get('/?apge-preview=waiting', environ_base=LAN).get_data(as_text=True)))

    def test_previews_never_touch_the_live_game_or_start_timers(self):
        server, client = self.server()
        before = threading.active_count()
        for _ in range(5):
            client.get('/?apge-preview=waiting')
        self.assertEqual(threading.active_count(), before)
        self.assertEqual((server.game.phase, server.game.players), ('lobby', {}))

    def test_dev_mode_never_runs_in_a_packaged_build(self):
        with mock.patch.object(sys, 'frozen', True, create=True):
            server, client = self.server()
        self.assertFalse(server.dev.enabled)
        self.assertEqual(client.get('/dev').status_code, 404)

    def test_dev_flag(self):
        self.assertTrue(dev_requested(['--dev']))
        self.assertFalse(dev_requested([]))
        with mock.patch.dict(os.environ, {'APGE_DEV': '1'}):
            self.assertTrue(dev_requested([]))

    def test_games_without_art_still_get_an_empty_library(self):
        server = PartyServer(Tiny(), base_dir=self.tmp.name, dev=False)
        self.assertEqual(self.config(server.app.test_client().get('/').get_data(as_text=True))['art'], {})


if __name__ == '__main__':
    unittest.main()
