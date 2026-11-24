/*
 * Developer Mode page (/dev): the art CMS and the screen gallery. See apge/dev.py.
 *
 * Art tab: Finder-style columns (category -> art -> versions) and a workspace with the
 * pixel editor and a live preview of every sample screen the art appears on. The preview
 * frames are the game's real pages in preview mode; the editor posts its unsaved drawing
 * to them (APGE.art in apge.js swaps it in), so they show the draft before it's saved.
 */
(function () {
    'use strict';

    const KEY = window.APGE_DEV.key;
    const $ = sel => document.querySelector(sel);
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
        ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const HOST = {w: 1280, h: 720}, PHONE = {w: 390, h: 844};

    let m = null;            // the manifest from /apge-dev/manifest
    let assets = {};         // id -> asset (with versions)
    let screens = {};        // id -> screen
    const cur = {category: null, asset: null, version: null};
    // The drawing in the workspace. version null = a new version that isn't saved yet
    let draft = null;
    let pad = null, dirty = false, zoom = 0;
    let previewFrames = [];  // iframes currently showing the draft

    // ---- server ------------------------------------------------------------------------

    function api(path, body) {
        const opts = body === undefined ? {} : {
            method: 'POST', body: JSON.stringify(body),
            headers: {'Content-Type': 'application/json', 'X-APGE-Dev': KEY}};
        return fetch(path, opts).then(r => r.json().catch(() => ({})).then(j => {
            if (!r.ok && j.ok !== false) return {ok: false, error: 'The game said ' + r.status + ' - is it still running with --dev?'};
            return j;
        })).catch(() => ({ok: false, error: "Can't reach the game - is it still running?"}));
    }

    function toast(text, bad) {
        const t = $('#toast');
        t.textContent = text;
        t.className = 'show' + (bad ? ' bad' : '');
        clearTimeout(toast.timer);
        toast.timer = setTimeout(() => { t.className = ''; }, 3500);
    }

    function loadManifest() {
        return api('/apge-dev/manifest').then(data => {
            if (data.ok === false) { toast(data.error, true); return; }
            m = data;
            assets = {};
            m.assets.forEach(a => { assets[a.id] = a; });
            screens = {};
            m.screens.forEach(s => { screens[s.id] = s; });
        });
    }

    const categories = () => [...new Set(m.assets.map(a => a.category))];
    const versionOf = (asset, id) => asset.versions.find(v => v.id === id);
    const thumbUrl = (assetId, v) => `/apge-dev/art/${assetId}/${v.id}.png?t=${v.mtime}`;

    // ---- columns ---------------------------------------------------------------------------

    function renderColumns() {
        const cats = categories();
        $('#col-categories ul').innerHTML = cats.map(c => {
            const list = m.assets.filter(a => a.category === c);
            const done = list.filter(a => a.selected).length;
            return `<li class="${c === cur.category ? 'on' : ''}" data-category="${esc(c)}">
                <span class="name">${esc(c)}</span><span class="count">${done}/${list.length}</span><span class="chev">▸</span></li>`;
        }).join('') || '<li class="dim">This game lists no art yet (PartyServer(art=...)).</li>';

        const list = m.assets.filter(a => a.category === cur.category);
        $('#col-assets ul').innerHTML = list.map(a => {
            const v = a.selected && versionOf(a, a.selected);
            return `<li class="${a.id === cur.asset ? 'on' : ''}" data-asset="${esc(a.id)}">
                ${v ? `<img class="thumb" src="${thumbUrl(a.id, v)}" alt="">` : '<span class="thumb empty"></span>'}
                <span class="name">${esc(a.name)}<small>${v ? 'in game: ' + esc(v.id) : 'no art yet'}</small></span><span class="chev">▸</span></li>`;
        }).join('');

        const asset = assets[cur.asset];
        $('#col-versions').classList.toggle('off', !asset);
        if (!asset) { $('#col-versions ul').innerHTML = ''; return; }
        const unsaved = draft && draft.version === null
            ? `<li class="on unsaved"><span class="thumb empty"></span><span class="name">New version<small>not saved yet</small></span></li>` : '';
        $('#col-versions ul').innerHTML = unsaved + asset.versions.slice().reverse().map(v => `
            <li class="${v.id === cur.version ? 'on' : ''}" data-version="${esc(v.id)}">
                <img class="thumb" src="${thumbUrl(asset.id, v)}" alt="">
                <span class="name">${esc(v.id)}${v.label ? ' · ' + esc(v.label) : ''}
                    <small>${v.width}×${v.height}${v.kind === 'png' ? ' · PNG' : ''}</small></span>
                ${v.id === asset.selected ? '<span class="badge">in game</span>' : ''}</li>`).join('');
    }

    function confirmDiscard() {
        return !dirty || confirm('You have unsaved changes to this drawing. Throw them away?');
    }

    function openCategory(c) {
        cur.category = c;
        renderColumns();
    }

    function openAsset(id, versionId) {
        const asset = assets[id];
        if (!asset || (id === cur.asset && !versionId)) return Promise.resolve();
        if (id !== cur.asset && !confirmDiscard()) return Promise.resolve();
        if (id !== cur.asset) zoom = 0;
        cur.category = asset.category;
        cur.asset = id;
        const pick = (versionId && versionOf(asset, versionId) && versionId) || asset.selected ||
            (asset.versions.length ? asset.versions[asset.versions.length - 1].id : null);
        if (pick) return openVersion(pick, true);
        newBlank(asset.size[0], asset.size[1], true);
        return Promise.resolve();
    }

    function openVersion(id, force) {
        if (!force && id !== cur.version && !confirmDiscard()) return Promise.resolve();
        const asset = assets[cur.asset];
        return api(`/apge-dev/art/${asset.id}/${id}`).then(v => {
            if (v.ok === false) { toast(v.error, true); return; }
            draft = {version: id, kind: v.kind, width: v.width, height: v.height, label: v.label || '',
                     pixelated: v.pixelated !== false, mtime: v.mtime,
                     pixels: v.kind === 'pixel' ? v.frames[0].join('') : null};
            cur.version = id;
            dirty = false;
            render();
        });
    }

    function newBlank(width, height, force, pixels, from) {
        if (!force && !confirmDiscard()) return;
        draft = {version: null, kind: 'pixel', width: width, height: height, label: '', pixelated: true,
                 pixels: pixels || '.'.repeat(width * height), from: from || null};
        cur.version = null;
        dirty = !!pixels;
        render();
    }

    // ---- workspace -------------------------------------------------------------------------

    // The address bar says what's open (#art/<asset>/<version> or #screens), so a link or
    // a reload comes back to it. Our own updates mustn't re-open what's already open.
    let ownHash = '';
    function setHash(hash) {
        ownHash = hash;
        location.replace(hash);
    }

    function route() {
        const [tab, assetId, version] = location.hash.slice(1).split('/');
        if (tab === 'screens') { showTab('screens'); return true; }
        if (tab === 'art' && assets[assetId]) { showTab('art'); openAsset(assetId, version); return true; }
        return false;
    }
    window.addEventListener('hashchange', () => { if (m && location.hash !== ownHash) route(); });

    function render() {
        renderColumns();
        renderWorkspace();
        setHash('#art/' + cur.asset + (cur.version ? '/' + cur.version : ''));
    }

    function renderWorkspace() {
        const asset = assets[cur.asset];
        const ws = $('#workspace');
        if (!asset || !draft) { ws.innerHTML = '<p class="dim">Pick some art on the left.</p>'; return; }
        const saved = draft.version !== null;
        const inGame = saved && asset.selected === draft.version;
        const png = draft.kind === 'png';
        ws.innerHTML = `
            <div class="ws-head">
                <div>
                    <h2>${esc(asset.name)}</h2>
                    <p class="dim">${esc(asset.id)} · starts at ${asset.size[0]}×${asset.size[1]}${asset.notes ? ' · ' + esc(asset.notes) : ''}</p>
                </div>
                <div class="ws-version">
                    <b>${saved ? esc(draft.version) : 'New version'}</b>
                    <input id="label" maxlength="60" placeholder="label" value="${esc(draft.label)}">
                    <span class="size">${draft.width}×${draft.height}</span>
                    ${inGame ? '<span class="badge">in game</span>' : ''}
                    <span class="unsaved-note" id="unsaved">${dirty ? 'unsaved changes' : ''}</span>
                </div>
            </div>
            <div class="toolbar">
                <button type="button" data-do="save" class="primary" ${png ? 'disabled title="Imported pictures can\'t be edited - Save As makes an editable copy"' : saved && !dirty ? 'disabled' : ''}>Save</button>
                <button type="button" data-do="save-as">Save As…</button>
                <button type="button" data-do="select" ${!saved || inGame ? 'disabled' : ''}>Use in game</button>
                <span class="sep"></span>
                <button type="button" data-do="resize" ${png ? 'disabled' : ''}>Resize canvas…</button>
                <span class="zoom">${png ? '' : '<button type="button" data-do="zoom-out">−</button><button type="button" data-do="zoom-fit">fit</button><button type="button" data-do="zoom-in">+</button>'}</span>
                <span class="sep"></span>
                <button type="button" data-do="download">Download PNG</button>
            </div>
            <div class="ws-body">
                <div class="editor" id="editor"></div>
                <div class="previews">
                    <h3>Live preview <small class="dim">${saved && !dirty ? '' : '(showing your unsaved drawing)'}</small></h3>
                    <div id="previews"></div>
                </div>
            </div>`;
        if (png) {
            $('#editor').innerHTML = `<div class="png-view"><img src="/apge-dev/art/${asset.id}/${draft.version}.png?t=${draft.mtime}"
                    class="${draft.pixelated ? 'pixelated' : ''}" alt=""></div>
                <label class="check"><input type="checkbox" id="pixelated" ${draft.pixelated ? 'checked' : ''}> Crisp pixels when scaled up (turn off for painted pictures)</label>
                <p class="dim">An imported picture. Save As turns it into pixel art you can edit here, using the game's palette${draft.width > m.max_pixel_side || draft.height > m.max_pixel_side ? ` (it must be ${m.max_pixel_side}×${m.max_pixel_side} or smaller)` : ''}.</p>`;
        } else {
            mountEditor();
        }
        renderPreviews(asset);
    }

    /** The biggest whole-pixel zoom that fits the drawing beside the previews. */
    function fitZoom() {
        const roomW = Math.max(240, $('#workspace').clientWidth * 0.6 - 30);
        const roomH = Math.max(200, window.innerHeight - 330);
        return Math.max(2, Math.min(24, Math.floor(Math.min(roomW / draft.width, roomH / draft.height))));
    }

    function mountEditor() {
        if (!zoom) zoom = fitZoom();
        pad = PixelArt.mountPad($('#editor'), {
            width: draft.width, height: draft.height, palette: m.palette, chars: m.chars,
            value: draft.pixels, cell: zoom, redo: true,
            onChange: pixels => {
                draft.pixels = pixels;
                if (!dirty) {
                    dirty = true;
                    $('#unsaved').textContent = 'unsaved changes';
                    $('[data-do=save]').disabled = false;
                }
                schedulePreview();
            },
        });
    }

    // ---- live preview ------------------------------------------------------------------------

    function frameHtml(screen, scale) {
        const size = screen.role === 'host' ? HOST : PHONE;
        return `<div class="frame ${screen.role}" style="width:${Math.round(size.w * scale)}px;height:${Math.round(size.h * scale)}px">
            <iframe src="${screen.url}" loading="lazy" tabindex="-1" style="width:${size.w}px;height:${size.h}px;transform:scale(${scale})"></iframe></div>`;
    }

    function renderPreviews(asset) {
        const box = $('#previews');
        const list = asset.screens.map(id => screens[id]).filter(Boolean);
        if (!list.length) {
            box.innerHTML = '<p class="dim">This art isn\'t on any sample screen yet: add the screen ids to its Asset(screens=...).</p>';
            previewFrames = [];
            return;
        }
        // As big as the column allows, up to a third of full size
        const room = Math.max(200, box.clientWidth - 4);
        const scale = s => Math.min(s.role === 'host' ? 0.34 : 0.45, room / (s.role === 'host' ? HOST.w : PHONE.w));
        box.innerHTML = list.map(s => `<figure data-screen="${esc(s.id)}">${frameHtml(s, scale(s))}
            <figcaption>${esc(s.label)} <button type="button" class="link" data-zoom="${esc(s.id)}">enlarge</button></figcaption></figure>`).join('');
        previewFrames = [...box.querySelectorAll('iframe')];
        previewFrames.forEach(f => f.addEventListener('load', () => sendDraft(f)));
    }

    function draftInfo() {
        const asset = assets[cur.asset];
        if (!draft || !asset) return null;
        if (draft.kind === 'png') {
            return {url: `/apge-dev/art/${asset.id}/${draft.version}.png?t=${draft.mtime}`,
                    w: draft.width, h: draft.height, pixelated: draft.pixelated};
        }
        const canvas = PixelArt.image(draft.pixels, m.palette, m.chars, draft.width);
        return {url: canvas.toDataURL(), w: draft.width, h: draft.height, pixelated: true};
    }

    function sendDraft(frame) {
        const info = draftInfo();
        if (!info || !frame.contentWindow) return;
        frame.contentWindow.postMessage({apge: 'art', id: cur.asset, info: info}, location.origin);
    }

    let previewTimer = null;
    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(() => {
            previewFrames = previewFrames.filter(f => f.isConnected);
            previewFrames.forEach(sendDraft);
        }, 120);
    }

    function enlarge(screenId) {
        const s = screens[screenId], dlg = $('#dlg-screen');
        const size = s.role === 'host' ? HOST : PHONE;
        const scale = Math.min((window.innerWidth - 80) / size.w, (window.innerHeight - 140) / size.h, 1);
        dlg.querySelector('.title').textContent = s.label;
        dlg.querySelector('.frame-slot').innerHTML = frameHtml(s, scale).replace(' loading="lazy"', '');
        const frame = dlg.querySelector('iframe');
        // The big frame shows the draft too, while the art tab is open
        if (!$('#tab-art').classList.contains('on') || !(assets[cur.asset] || {screens: []}).screens.includes(screenId)) {
            dlg.showModal();
            return;
        }
        frame.addEventListener('load', () => sendDraft(frame));
        previewFrames.push(frame);
        dlg.showModal();
    }
    $('#dlg-screen').addEventListener('close', () => { $('#dlg-screen .frame-slot').innerHTML = ''; });

    // ---- saving ------------------------------------------------------------------------------------

    function afterSave(assetId, version, message) {
        dirty = false;
        return loadManifest().then(() => {
            cur.asset = null;   // so openAsset doesn't ask about the changes we just saved
            return openAsset(assetId, version);
        }).then(() => toast(message));
    }

    function save() {
        const asset = assets[cur.asset];
        if (!draft || draft.kind !== 'pixel' || (draft.version && !dirty)) return;
        const body = {width: draft.width, height: draft.height, frames: [draft.pixels], label: $('#label').value};
        if (draft.version) body.version = draft.version;
        else {
            body.from = draft.from;
            body.select = !asset.selected;   // the first art for something goes straight in game
        }
        api(`/apge-dev/art/${asset.id}/save`, body).then(r => {
            if (!r.ok) { toast(r.error, true); return; }
            afterSave(asset.id, r.version, `Saved ${asset.name} ${r.version}` + (body.select ? ' - it\'s in game now' : ''));
        });
    }

    /** Pixels for the current drawing; an imported PNG is matched to the palette first. */
    function draftPixels() {
        if (draft.kind === 'pixel') return Promise.resolve(draft.pixels);
        if (draft.width > m.max_pixel_side || draft.height > m.max_pixel_side) {
            return Promise.reject(new Error(`Only pictures up to ${m.max_pixel_side}×${m.max_pixel_side} can become pixel art.`));
        }
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(quantize(img));
            img.onerror = () => reject(new Error("Couldn't read that picture."));
            img.src = draftInfo().url;
        });
    }

    function quantize(img) {
        const c = document.createElement('canvas');
        c.width = img.naturalWidth;
        c.height = img.naturalHeight;
        const ctx = c.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const data = ctx.getImageData(0, 0, c.width, c.height).data;
        const rgb = m.palette.map(h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)));
        let out = '';
        for (let i = 0; i < data.length; i += 4) {
            if (data[i + 3] < 128) { out += '.'; continue; }
            let best = 0, bestD = Infinity;
            rgb.forEach((p, k) => {
                const d = (p[0] - data[i]) ** 2 + (p[1] - data[i + 1]) ** 2 + (p[2] - data[i + 2]) ** 2;
                if (d < bestD) { bestD = d; best = k; }
            });
            out += m.chars[best];
        }
        return out;
    }

    function saveAs() {
        const dlg = $('#dlg-save-as'), form = dlg.querySelector('form').elements;
        form.asset.innerHTML = categories().map(c => `<optgroup label="${esc(c)}">` +
            m.assets.filter(a => a.category === c).map(a =>
                `<option value="${esc(a.id)}" ${a.id === cur.asset ? 'selected' : ''}>${esc(a.name)}</option>`).join('') + '</optgroup>').join('');
        form.label.value = $('#label') ? $('#label').value : '';
        const syncCheck = () => { form.select.checked = !assets[form.asset.value].selected; };
        form.asset.onchange = syncCheck;
        syncCheck();
        dlg.querySelector('.error').textContent = '';
        dlg.returnValue = '';
        dlg.showModal();
        dlg.onclose = () => {
            if (dlg.returnValue !== 'ok') return;
            const target = form.asset.value, select = form.select.checked, label = form.label.value;
            const from = draft.version ? `${cur.asset}/${draft.version}` : draft.from;
            draftPixels().then(pixels => api(`/apge-dev/art/${target}/save`, {
                width: draft.width, height: draft.height, frames: [pixels], label: label, from: from, select: select,
            })).then(r => {
                if (!r.ok) { toast(r.error, true); return; }
                dirty = false;
                afterSave(target, r.version, `Saved as ${assets[target].name} ${r.version}` + (select ? ' (in game)' : ''));
            }).catch(e => toast(e.message, true));
        };
    }

    function useInGame() {
        const asset = assets[cur.asset];
        if (!draft || !draft.version) return;
        if (dirty && !confirm('Use the saved version in game? Your unsaved changes stay in the editor.')) return;
        api(`/apge-dev/art/${asset.id}/select`, {version: draft.version}).then(r => {
            if (!r.ok) { toast(r.error, true); return; }
            asset.selected = draft.version;
            renderColumns();
            renderWorkspace();
            toast(`${asset.name}: ${draft.version} is in game. Reload the host screen to see it.`);
        });
    }

    function resizeDialog() {
        const dlg = $('#dlg-resize'), form = dlg.querySelector('form').elements;
        form.width.max = form.height.max = m.max_pixel_side;
        form.width.value = draft.width;
        form.height.value = draft.height;
        dlg.returnValue = '';
        dlg.showModal();
        dlg.onclose = () => {
            if (dlg.returnValue !== 'ok') return;
            const w = Number(form.width.value), h = Number(form.height.value);
            if (!(w >= 1 && h >= 1 && w <= m.max_pixel_side && h <= m.max_pixel_side)) {
                toast(`Canvases can be 1 to ${m.max_pixel_side} pixels each way.`, true);
                return;
            }
            const [ax, ay] = form.anchor.value.split(',').map(Number);
            const ox = Math.round((w - draft.width) * ax), oy = Math.round((h - draft.height) * ay);
            const out = new Array(w * h).fill('.');
            for (let y = 0; y < draft.height; y++) {
                for (let x = 0; x < draft.width; x++) {
                    const nx = x + ox, ny = y + oy;
                    if (nx >= 0 && ny >= 0 && nx < w && ny < h) out[ny * w + nx] = draft.pixels[y * draft.width + x];
                }
            }
            draft.width = w;
            draft.height = h;
            draft.pixels = out.join('');
            dirty = true;
            zoom = 0;
            renderWorkspace();
            schedulePreview();
        };
    }

    function importPng(file) {
        const asset = assets[cur.asset];
        if (!file || !asset) return;
        if (!confirmDiscard()) return;
        const reader = new FileReader();
        reader.onload = () => api(`/apge-dev/art/${asset.id}/import`, {
            png: reader.result, label: file.name.replace(/\.[a-z]+$/i, '').slice(0, 60), select: !asset.selected,
        }).then(r => {
            if (!r.ok) { toast(r.error, true); return; }
            afterSave(asset.id, r.version, `Imported ${file.name} as ${r.version}`);
        });
        reader.readAsDataURL(file);
    }

    function download() {
        const asset = assets[cur.asset];
        const name = `${asset.id}-${draft.version || 'new'}.png`;
        const go = url => { const a = document.createElement('a'); a.href = url; a.download = name; a.click(); };
        if (draft.kind === 'png') { go(draftInfo().url); return; }
        // Scaled up so the download is usable outside the game too
        const scale = Math.max(1, Math.floor(512 / Math.max(draft.width, draft.height)));
        const c = document.createElement('canvas');
        c.width = draft.width * scale;
        c.height = draft.height * scale;
        const ctx = c.getContext('2d');
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(PixelArt.image(draft.pixels, m.palette, m.chars, draft.width), 0, 0, c.width, c.height);
        go(c.toDataURL('image/png'));
    }

    // ---- screens tab ------------------------------------------------------------------------------------

    function renderGallery() {
        const usedBy = {};
        m.assets.forEach(a => a.screens.forEach(id => { (usedBy[id] = usedBy[id] || []).push(a); }));
        const groups = [...new Set(m.screens.map(s => s.group))];
        $('#gallery').innerHTML = groups.map(g => `${g ? `<h2>${esc(g)}</h2>` : ''}<div class="grid">` +
            m.screens.filter(s => s.group === g).map(s => `
                <figure>${frameHtml(s, s.role === 'host' ? 0.25 : 0.36)}
                    <figcaption><b>${esc(s.label)}</b> <small class="dim">${s.role === 'host' ? 'host screen' : 'phone'} · ${esc(s.id)}</small>
                        <button type="button" class="link" data-zoom="${esc(s.id)}">enlarge</button>
                        <a href="${esc(s.url)}" target="_blank" rel="noopener">open ↗</a>
                        <span class="chips">${(usedBy[s.id] || []).map(a =>
                            `<a href="#art/${esc(a.id)}" class="chip ${a.selected ? 'done' : ''}">${esc(a.name)}</a>`).join('')}</span>
                    </figcaption></figure>`).join('') + '</div>').join('') ||
            '<p class="dim">This game has no sample screens yet (PartyServer(screens=...)).</p>';
    }

    function showTab(name) {
        document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
        document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-' + name));
        if (name === 'screens') {
            if (!$('#gallery').innerHTML) renderGallery();   // fresh frames each visit: they show art saved since
            setHash('#screens');
        } else {
            $('#gallery').innerHTML = '';
            if (cur.asset) setHash('#art/' + cur.asset + (cur.version ? '/' + cur.version : ''));
        }
    }

    // ---- events ---------------------------------------------------------------------------------------

    document.addEventListener('click', e => {
        const t = e.target.closest('[data-tab],[data-category],[data-asset],[data-version],[data-do],[data-zoom],a.chip');
        if (!t) return;
        if (t.dataset.tab) showTab(t.dataset.tab);
        else if (t.dataset.zoom) enlarge(t.dataset.zoom);
        else if (t.matches('a.chip')) { e.preventDefault(); showTab('art'); openAsset(t.getAttribute('href').split('/')[1]); }
        else if (t.dataset.category) openCategory(t.dataset.category);
        else if (t.dataset.asset) openAsset(t.dataset.asset);
        else if (t.dataset.version) openVersion(t.dataset.version);
        else {
            const act = t.dataset.do, asset = assets[cur.asset];
            if (act === 'new' && asset) newBlank(asset.size[0], asset.size[1]);
            if (!draft) return;
            if (act === 'save') save();
            if (act === 'save-as') saveAs();
            if (act === 'select') useInGame();
            if (act === 'resize') resizeDialog();
            if (act === 'download') download();
            if (act === 'zoom-in' || act === 'zoom-out') {
                zoom = Math.max(2, Math.min(48, pad.zoom() + (act === 'zoom-in' ? 2 : -2)));
                pad.zoom(zoom);
            }
            if (act === 'zoom-fit') { zoom = fitZoom(); pad.zoom(zoom); }
        }
    });

    document.addEventListener('change', e => {
        if (e.target.id === 'import') { importPng(e.target.files[0]); e.target.value = ''; }
        if (e.target.id === 'label' && draft && draft.version) {
            const asset = assets[cur.asset], label = e.target.value;
            api(`/apge-dev/art/${asset.id}/${draft.version}/info`, {label: label}).then(r => {
                if (!r.ok) { toast(r.error, true); return; }
                draft.label = label;
                const v = versionOf(asset, draft.version);
                if (v) v.label = label;
                renderColumns();
            });
        }
        if (e.target.id === 'pixelated' && draft) {
            const asset = assets[cur.asset], on = e.target.checked;
            api(`/apge-dev/art/${asset.id}/${draft.version}/info`, {pixelated: on}).then(r => {
                if (!r.ok) { toast(r.error, true); return; }
                draft.pixelated = on;
                $('#editor img').classList.toggle('pixelated', on);
                schedulePreview();
            });
        }
    });

    document.addEventListener('keydown', e => {
        if (!(e.metaKey || e.ctrlKey) || !draft || !$('#tab-art').classList.contains('on')) return;
        if (document.querySelector('dialog[open]')) return;
        const k = e.key.toLowerCase();
        if (k === 's') { e.preventDefault(); if (e.shiftKey) saveAs(); else save(); }
        const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
        if (k === 'z' && pad && !typing) { e.preventDefault(); if (e.shiftKey) pad.redo(); else pad.undo(); }
    });

    window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

    // ---- start --------------------------------------------------------------------------------------

    loadManifest().then(() => {
        if (!m || route()) return;
        cur.category = categories()[0] || null;
        renderColumns();
    });
})();
