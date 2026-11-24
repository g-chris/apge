"""
Game art: every picture a game needs, each with any number of saved versions, one of
which is "in game".

A game lists its art as `Asset`s and passes them to PartyServer:

    from apge.art import Asset
    ART = [
        Asset('agent', 'Agent Kevin', 'Characters', size=(32, 48), screens=['frisk']),
        Asset('map', 'Map of Australia and NZ', 'Map', size=(192, 108), screens=['map']),
    ]
    PartyServer(MyGame(), base_dir, art=ART, palette=PALETTE)

Pages get the in-game version of each asset from `APGE.art` (apge.js); until an asset
has one, `APGE.art.has(id)` is false and the game draws its placeholder.

On disk, next to the game (commit it; packaged builds bundle it):

    art/<asset id>/asset.json   {"selected": "v2"}
    art/<asset id>/v1.json      a version: pixel art (palette indices, git-diffable rows)
    art/<asset id>/v2.json      ...or an imported PNG's details, with the image in v2.png

Pixel art uses the same format as everything else drawn with pixelart.js: one character
per pixel, '.' = transparent, otherwise an index into the palette written with
PIXEL_CHARS. Versions keep a list of frames so animation can be added without changing
the format; today only the first frame is shown.
"""
import io
import json
import os
import re
import threading
from datetime import datetime

PIXEL_CHARS = '0123456789abcdefghijklmnopqrstuvwxyz'
MAX_PIXEL_SIDE = 256      # biggest pixel-art canvas the editor can work with
MAX_PNG_SIDE = 2048       # imported pictures
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_FRAMES = 64
MAX_LABEL = 60

ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
VERSION_RE = re.compile(r'^v([1-9][0-9]{0,4})$')


class ArtError(ValueError):
    """A save or import the library refused; the message is shown in Developer Mode."""


class Asset:
    """One picture the game needs.

    id:       stable key the game's code uses (lowercase letters, digits, _ and -)
    name:     what it is, shown in Developer Mode and in placeholders
    category: groups assets in Developer Mode's first column
    size:     (width, height) a new blank version starts at; any version can differ
    screens:  ids of the Developer Mode sample screens that show this art (live preview)
    notes:    anything the artist should know (where it sits, what it must read as)
    """

    def __init__(self, id, name, category, size, screens=(), notes=''):
        if not ID_RE.match(id):
            raise ValueError(f'Art id {id!r}: use lowercase letters, digits, _ and -')
        width, height = size
        if not (1 <= width <= MAX_PIXEL_SIDE and 1 <= height <= MAX_PIXEL_SIDE):
            raise ValueError(f'Art {id!r}: size must be 1-{MAX_PIXEL_SIDE} pixels each way')
        self.id, self.name, self.category = id, name, category
        self.size = (int(width), int(height))
        self.screens = list(screens)
        self.notes = notes

    def to_json(self):
        return {'id': self.id, 'name': self.name, 'category': self.category,
                'size': list(self.size), 'screens': self.screens, 'notes': self.notes}


def _int_in(value, low, high, what):
    # bool is an int in Python; a JSON true must not become a 1-pixel canvas
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ArtError(f'{what} must be a whole number from {low} to {high}.')
    return value


def _label(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ArtError('The label must be text.')
    return ' '.join(value.split())[:MAX_LABEL]


def _hex_to_rgba(colour):
    colour = colour.lstrip('#')
    return (int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16), 255)


class ArtLibrary:
    """Reads and writes a game's art folder. Writing is only ever done from Developer Mode."""

    def __init__(self, root, assets, palette=None):
        self.root = root
        self.assets = {}
        for asset in assets:
            if asset.id in self.assets:
                raise ValueError(f'Art id {asset.id!r} is listed twice')
            self.assets[asset.id] = asset
        self.palette = list(palette or [])
        if len(self.palette) > len(PIXEL_CHARS):
            raise ValueError(f'Pixel art palettes hold at most {len(PIXEL_CHARS)} colours')
        self.chars = PIXEL_CHARS[:len(self.palette)]
        self._rgba = [_hex_to_rgba(c) for c in self.palette]
        self._pixel_re = re.compile('^[.%s]*$' % re.escape(self.chars))
        self._renders = {}   # (path, mtime) -> PNG bytes
        self._write_lock = threading.Lock()

    # ---- paths ---------------------------------------------------------------

    def asset(self, asset_id):
        asset = self.assets.get(asset_id) if isinstance(asset_id, str) else None
        if not asset:
            raise ArtError('No such art in this game.')
        return asset

    def _dir(self, asset_id):
        return os.path.join(self.root, self.asset(asset_id).id)

    def _version_path(self, asset_id, version, ext='.json'):
        if not isinstance(version, str) or not VERSION_RE.match(version):
            raise ArtError('No such version.')
        return os.path.join(self._dir(asset_id), version + ext)

    # ---- reading ---------------------------------------------------------------

    def version_ids(self, asset_id):
        folder = self._dir(asset_id)
        if not os.path.isdir(folder):
            return []
        found = [VERSION_RE.match(name[:-5]) for name in os.listdir(folder) if name.endswith('.json')]
        return ['v%d' % n for n in sorted(int(m.group(1)) for m in found if m)]

    def read_version(self, asset_id, version):
        path = self._version_path(asset_id, version)
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            raise ArtError('No such version.')
        data['id'] = version
        data['mtime'] = int(os.path.getmtime(path))
        if data.get('kind') == 'png':
            png = self._version_path(asset_id, version, '.png')
            if os.path.exists(png):
                data['mtime'] = max(data['mtime'], int(os.path.getmtime(png)))
        return data

    def version_info(self, asset_id, version):
        """A version's details without its pixels (for lists)."""
        data = self.read_version(asset_id, version)
        data.pop('frames', None)
        return data

    def selected(self, asset_id):
        """The id of the version shown in game, or None."""
        try:
            with open(os.path.join(self._dir(asset_id), 'asset.json'), encoding='utf-8') as f:
                version = json.load(f).get('selected')
        except (OSError, ValueError):
            return None
        return version if version in self.version_ids(asset_id) else None

    def client_config(self):
        """What every page gets as APGE.art: each asset's name and its in-game picture."""
        out = {}
        for asset_id, asset in self.assets.items():
            entry = {'name': asset.name, 'url': None}
            version = self.selected(asset_id)
            if version:
                try:
                    info = self.version_info(asset_id, version)
                except ArtError:
                    info = None
                if info:
                    entry.update(url=f'/apge-art/{asset_id}.png?v={version}.{info["mtime"]}',
                                 w=info['width'], h=info['height'],
                                 pixelated=bool(info.get('pixelated', True)))
            out[asset_id] = entry
        return out

    def manifest(self):
        """Everything Developer Mode lists: assets, their versions and which is in game."""
        assets = []
        for asset_id, asset in self.assets.items():
            entry = asset.to_json()
            entry['selected'] = self.selected(asset_id)
            entry['versions'] = []
            for version in self.version_ids(asset_id):
                try:
                    entry['versions'].append(self.version_info(asset_id, version))
                except ArtError:
                    continue  # a half-written or hand-broken file: skip it rather than fail the page
            assets.append(entry)
        return {'palette': self.palette, 'chars': self.chars, 'assets': assets,
                'max_pixel_side': MAX_PIXEL_SIDE}

    def render_png(self, asset_id, version=None):
        """PNG bytes for a version (default: the one in game), one image pixel per art pixel."""
        version = version or self.selected(asset_id)
        if not version:
            raise ArtError('This art has no version in game yet.')
        data = self.read_version(asset_id, version)
        if data.get('kind') == 'png':
            with open(self._version_path(asset_id, version, '.png'), 'rb') as f:
                return f.read()
        key = (asset_id, version, data['mtime'])
        if key not in self._renders:
            if len(self._renders) > 256:
                self._renders.clear()
            self._renders[key] = self._pixel_png(data['width'], data['height'], ''.join(data['frames'][0]))
        return self._renders[key]

    def _pixel_png(self, width, height, pixels):
        from PIL import Image   # Pillow ships with qrcode[pil], an APGE dependency
        clear = (0, 0, 0, 0)
        lookup = {c: self._rgba[i] for i, c in enumerate(self.chars)}
        img = Image.new('RGBA', (width, height))
        img.putdata([lookup.get(c, clear) for c in pixels])
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    # ---- writing (Developer Mode only) ---------------------------------------------

    def _next_version(self, asset_id):
        ids = self.version_ids(asset_id)
        return 'v%d' % (int(ids[-1][1:]) + 1 if ids else 1)

    def _write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
            f.write('\n')
        os.replace(tmp, path)   # never leave a half-written version if the save is interrupted

    def save_pixels(self, asset_id, data, version=None):
        """Save pixel art. version=None makes a new version; otherwise overwrites that one.

        data: {width, height, frames: [pixel string, ...], label?, from?, pixelated?}
        Returns the version id.
        """
        self.asset(asset_id)
        if not isinstance(data, dict):
            raise ArtError('Nothing to save.')
        width = _int_in(data.get('width'), 1, MAX_PIXEL_SIDE, 'Width')
        height = _int_in(data.get('height'), 1, MAX_PIXEL_SIDE, 'Height')
        frames = data.get('frames')
        if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
            raise ArtError(f'A version needs 1 to {MAX_FRAMES} frames.')
        rows = []
        for frame in frames:
            if not isinstance(frame, str) or len(frame) != width * height or not self._pixel_re.match(frame):
                raise ArtError("That drawing didn't come through - try saving again.")
            rows.append([frame[y * width:(y + 1) * width] for y in range(height)])
        source = data.get('from')
        if source is not None and (not isinstance(source, str) or not re.match(r'^[a-z0-9_-]+/v[0-9]+$', source)):
            source = None
        with self._write_lock:
            if version is None:
                version = self._next_version(asset_id)
                created = datetime.now().isoformat(timespec='seconds')
                label = _label(data.get('label'))
            else:
                old = self.read_version(asset_id, version)
                if old.get('kind') != 'pixel':
                    raise ArtError('Imported pictures can\'t be overwritten - use Save As.')
                created = old.get('created')
                label = _label(data['label']) if 'label' in data else old.get('label', '')
                source = old.get('from')
            record = {'kind': 'pixel', 'label': label, 'width': width, 'height': height,
                      'pixelated': True, 'fps': None, 'frames': rows, 'created': created}
            if source:
                record['from'] = source
            self._write_json(self._version_path(asset_id, version), record)
        return version

    def import_png(self, asset_id, png_bytes, label=None):
        """Store an uploaded picture as a new version. Returns the version id."""
        from PIL import Image, UnidentifiedImageError
        self.asset(asset_id)
        if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
            raise ArtError('No picture was uploaded.')
        if len(png_bytes) > MAX_PNG_BYTES:
            raise ArtError(f'Pictures must be under {MAX_PNG_BYTES // (1024 * 1024)} MB.')
        try:
            Image.open(io.BytesIO(png_bytes)).verify()
            img = Image.open(io.BytesIO(png_bytes))
            width, height = img.size
            if width > MAX_PNG_SIDE or height > MAX_PNG_SIDE:
                raise ArtError(f'Pictures can be at most {MAX_PNG_SIDE} pixels each way.')
            img = img.convert('RGBA')
        except (UnidentifiedImageError, OSError, SyntaxError):
            raise ArtError("That file isn't a picture APGE can read (use PNG).")
        buf = io.BytesIO()
        img.save(buf, format='PNG')   # re-encode: only pixels are kept, never the upload itself
        with self._write_lock:
            version = self._next_version(asset_id)
            png_path = self._version_path(asset_id, version, '.png')
            os.makedirs(os.path.dirname(png_path), exist_ok=True)
            with open(png_path, 'wb') as f:
                f.write(buf.getvalue())
            self._write_json(self._version_path(asset_id, version), {
                'kind': 'png', 'label': _label(label), 'width': width, 'height': height,
                # Small pictures are almost certainly pixel art: keep their edges crisp
                'pixelated': max(width, height) <= MAX_PIXEL_SIDE,
                'created': datetime.now().isoformat(timespec='seconds')})
        return version

    def update_info(self, asset_id, version, changes):
        """Rename a version or switch smooth/pixelated scaling."""
        if not isinstance(changes, dict):
            raise ArtError('Nothing to change.')
        with self._write_lock:
            path = self._version_path(asset_id, version)
            record = self.read_version(asset_id, version)
            record.pop('id'), record.pop('mtime')
            if 'label' in changes:
                record['label'] = _label(changes['label'])
            if 'pixelated' in changes:
                if not isinstance(changes['pixelated'], bool):
                    raise ArtError('Pixelated must be true or false.')
                record['pixelated'] = changes['pixelated']
            self._write_json(path, record)

    def select(self, asset_id, version):
        """Make a version the one shown in game."""
        if version not in self.version_ids(asset_id):
            raise ArtError('No such version.')
        with self._write_lock:
            self._write_json(os.path.join(self._dir(asset_id), 'asset.json'), {'selected': version})
