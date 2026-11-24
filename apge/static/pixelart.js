/*
 * Pixel art: the editor players draw avatars with, and Developer Mode's art editor.
 * A touch/pen/mouse port of in-a-big-forest's art_tools.py: a grid, a palette, pencil,
 * eraser, fill, colour picker, mirror and undo. Load it with <script src="/apge/pixelart.js">.
 *
 * Art is a string, one char per cell row by row: '.' = transparent, otherwise an index
 * into the palette written with `chars` (apge.art.PIXEL_CHARS, '0'-'9' then 'a'-'z').
 * Square art (avatars) needs no width; anything else passes its width.
 *
 *   PixelArt.mountPad(el, {grid | width+height, palette, chars, value, onChange, maxPx, cell})
 *       -> {get, set, clear, undo, redo, resize, zoom, width, height}
 *   PixelArt.image(art, palette, chars, width?)                    -> cached <canvas>
 *   PixelArt.draw(ctx, art, palette, chars, x, y, size)            (square art)
 *   PixelArt.element(art, palette, chars, px)                      -> <canvas> for HTML lists
 */
(function () {
    'use strict';

    const cache = new Map();

    function gridSize(art) { return Math.round(Math.sqrt(art.length)); }

    /** A canvas with one pixel per cell (scale it up with imageSmoothingEnabled = false). */
    function image(art, palette, chars, width) {
        const w = width || gridSize(art), h = Math.max(1, Math.round(art.length / w));
        const key = w + '|' + art + '|' + chars + '|' + palette.join();
        if (cache.has(key)) return cache.get(key);
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        for (let i = 0; i < art.length; i++) {
            const idx = chars.indexOf(art[i]);
            if (idx < 0) continue;
            ctx.fillStyle = palette[idx];
            ctx.fillRect(i % w, Math.floor(i / w), 1, 1);
        }
        if (cache.size > 200) cache.clear();
        cache.set(key, canvas);
        return canvas;
    }

    function draw(ctx, art, palette, chars, x, y, size) {
        if (!art) return;
        const smooth = ctx.imageSmoothingEnabled;
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(image(art, palette, chars), x, y, size, size);
        ctx.imageSmoothingEnabled = smooth;
    }

    /** An <img>-like element for HTML lists (a scaled canvas). */
    function element(art, palette, chars, px) {
        const c = document.createElement('canvas');
        const n = gridSize(art || '.');
        c.width = c.height = n;
        c.className = 'pixel-head';
        c.style.width = c.style.height = px + 'px';
        if (art) c.getContext('2d').drawImage(image(art, palette, chars), 0, 0);
        return c;
    }

    function mountPad(container, opts) {
        const W = opts.width || opts.grid, H = opts.height || opts.grid;
        const palette = opts.palette, chars = opts.chars;
        // A fresh element per mount, so remounting (e.g. a new grid size) never stacks listeners
        container.innerHTML = '';
        const root = document.createElement('div');
        container.appendChild(root);
        let cells = (opts.value && opts.value.length === W * H ? opts.value : '.'.repeat(W * H)).split('');
        let color = 0, tool = 'pencil', mirror = false, cellPx = opts.cell || 0;
        const undo = [], redo = [];

        root.classList.add('pad');
        root.innerHTML =
            '<div class="pad-tools">' +
            ['pencil', 'eraser', 'fill', 'picker'].map(t => `<button type="button" data-tool="${t}">${t}</button>`).join('') +
            '<button type="button" data-opt="mirror">mirror</button>' +
            '<button type="button" data-opt="undo">undo</button>' +
            (opts.redo ? '<button type="button" data-opt="redo">redo</button>' : '') +
            '<button type="button" data-opt="clear">clear</button></div>' +
            '<div class="pad-scroll"><canvas class="pad-canvas"></canvas></div>' +
            '<div class="pad-palette">' + palette.map((c, i) =>
                `<button type="button" class="swatch" data-color="${i}" style="background:${c}" title="${c}"></button>`).join('') + '</div>';
        const canvas = root.querySelector('canvas');
        const ctx = canvas.getContext('2d');

        function resize() {
            let cell = cellPx;
            if (!cell) {
                const width = Math.min(container.clientWidth || 320, opts.maxPx || 420);
                cell = Math.floor(width / Math.max(W, H));
            }
            cell = Math.max(2, cell);
            canvas.width = cell * W;
            canvas.height = cell * H;
            render();
        }

        function render() {
            const cell = canvas.width / W;
            for (let i = 0; i < W * H; i++) {
                const x = (i % W) * cell, y = Math.floor(i / W) * cell;
                const idx = chars.indexOf(cells[i]);
                ctx.fillStyle = idx >= 0 ? palette[idx] : (((i % W) + Math.floor(i / W)) % 2 ? '#5a5a5a' : '#6e6e6e');
                ctx.fillRect(x, y, cell, cell);
            }
            if (opts.gridLines !== false && cell >= 8) {
                ctx.strokeStyle = 'rgba(0,0,0,0.25)';
                ctx.lineWidth = 1;
                for (let k = 1; k < W; k++) {
                    ctx.beginPath(); ctx.moveTo(k * cell + 0.5, 0); ctx.lineTo(k * cell + 0.5, canvas.height); ctx.stroke();
                }
                for (let k = 1; k < H; k++) {
                    ctx.beginPath(); ctx.moveTo(0, k * cell + 0.5); ctx.lineTo(canvas.width, k * cell + 0.5); ctx.stroke();
                }
            }
            if (mirror) {
                ctx.strokeStyle = 'rgba(255,220,0,0.7)';
                ctx.beginPath(); ctx.moveTo(canvas.width / 2, 0); ctx.lineTo(canvas.width / 2, canvas.height); ctx.stroke();
            }
            root.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('on', b.dataset.tool === tool));
            root.querySelectorAll('[data-color]').forEach(b => b.classList.toggle('on', Number(b.dataset.color) === color));
            root.querySelector('[data-opt=mirror]').classList.toggle('on', mirror);
        }

        function changed() {
            render();
            if (opts.onChange) opts.onChange(cells.join(''));
        }

        function remember() {
            undo.push(cells.join(''));
            if (undo.length > 100) undo.shift();
            redo.length = 0;
        }

        function paint(i, value) {
            cells[i] = value;
            if (mirror) {
                const x = i % W, y = Math.floor(i / W);
                cells[y * W + (W - 1 - x)] = value;
            }
        }

        /** Paint every cell on the line between two cells, so a fast stroke has no gaps. */
        function paintLine(from, to, value) {
            let x0 = from % W, y0 = Math.floor(from / W);
            const x1 = to % W, y1 = Math.floor(to / W);
            const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
            const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
            let err = dx + dy;
            for (;;) {
                paint(y0 * W + x0, value);
                if (x0 === x1 && y0 === y1) break;
                const e2 = 2 * err;
                if (e2 >= dy) { err += dy; x0 += sx; }
                if (e2 <= dx) { err += dx; y0 += sy; }
            }
        }

        function fill(start, value) {
            const target = cells[start];
            if (target === value) return;
            const stack = [start];
            while (stack.length) {
                const i = stack.pop();
                if (cells[i] !== target) continue;
                cells[i] = value;
                const x = i % W, y = Math.floor(i / W);
                if (x > 0) stack.push(i - 1);
                if (x < W - 1) stack.push(i + 1);
                if (y > 0) stack.push(i - W);
                if (y < H - 1) stack.push(i + W);
            }
        }

        function cellAt(e) {
            const r = canvas.getBoundingClientRect();
            const x = Math.floor((e.clientX - r.left) / r.width * W);
            const y = Math.floor((e.clientY - r.top) / r.height * H);
            return x >= 0 && y >= 0 && x < W && y < H ? y * W + x : -1;
        }

        let drawing = false, last = -1;
        const ink = () => tool === 'eraser' ? '.' : chars[color];
        canvas.addEventListener('pointerdown', e => {
            const i = cellAt(e);
            if (i < 0) return;
            e.preventDefault();
            if (tool === 'picker') {
                const idx = chars.indexOf(cells[i]);
                if (idx >= 0) { color = idx; tool = 'pencil'; } else tool = 'eraser';
                render();
                return;
            }
            remember();
            if (tool === 'fill') { fill(i, ink()); changed(); return; }
            drawing = true;
            last = i;
            canvas.setPointerCapture(e.pointerId);
            paint(i, ink());
            changed();
        });
        canvas.addEventListener('pointermove', e => {
            if (!drawing) return;
            const i = cellAt(e);
            if (i >= 0 && i !== last) { paintLine(last, i, ink()); last = i; changed(); }
        });
        ['pointerup', 'pointercancel'].forEach(t => canvas.addEventListener(t, () => { drawing = false; }));

        function step(from, to) {
            if (!from.length) return;
            to.push(cells.join(''));
            cells = from.pop().split('');
            changed();
        }

        root.addEventListener('click', e => {
            const b = e.target.closest('button');
            if (!b || !root.contains(b)) return;
            if (b.dataset.tool) tool = b.dataset.tool;
            if (b.dataset.color) { color = Number(b.dataset.color); if (tool === 'eraser' || tool === 'picker') tool = 'pencil'; }
            if (b.dataset.opt === 'mirror') mirror = !mirror;
            if (b.dataset.opt === 'undo') { step(undo, redo); return; }
            if (b.dataset.opt === 'redo') { step(redo, undo); return; }
            if (b.dataset.opt === 'clear') { remember(); cells = cells.map(() => '.'); }
            changed();
        });
        const onResize = () => { if (root.isConnected) resize(); else window.removeEventListener('resize', onResize); };
        window.addEventListener('resize', onResize);
        resize();

        return {
            width: W,
            height: H,
            get: () => cells.join(''),
            set: art => { remember(); cells = art.split(''); changed(); },
            clear: () => { remember(); cells = cells.map(() => '.'); changed(); },
            undo: () => step(undo, redo),
            redo: () => step(redo, undo),
            /** Cell size in screen pixels; 0 = fit the container. */
            zoom: px => { if (px !== undefined) { cellPx = px; resize(); } return canvas.width / W; },
            resize: resize,
        };
    }

    window.PixelArt = {image, draw, element, mountPad};
})();
