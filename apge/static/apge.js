/*
 * APGE client - connects a host screen or a phone to an APGE server.
 *
 *   const apge = APGE.connect();          // role comes from the page (host at /, player at /player)
 *   apge.on('state', view => render(view));
 *   apge.screens({lobby: 'lobby-screen', answer: 'answer-screen', '*': 'wait-screen'});
 *   apge.join('My Name');                 // players
 *   apge.action('submit', {text}).then(ack => ...);
 *   apge.ready();                         // "Continue" in ready phases
 *   apge.start(); apge.skip(); apge.reset(); apge.quit();   // host computer (and the VIP's
 *                                                           // phone, for the game's vip_commands)
 *   apge.phaseStarted                     // Date.now() when this screen saw the phase begin
 *   apge.bindCountdown('timer', null, {urgentAt: 10, onUrgent: () => APGE.sound.beep()});
 *   APGE.sound.allowed()                  // false until someone clicks/taps the page
 *
 *   APGE.art.has('agent')                 // the game's art (see apge/art.py): false until the
 *   APGE.art.url('agent')                 // asset has a version "in game" - draw a placeholder
 *   APGE.art.draw(ctx, 'agent', x, y, w, h)   // canvas: fit inside the box; false if no art
 *
 * Developer Mode opens pages with ?apge-preview=<screen>: no socket, a sample state from the
 * server, and art drafts sent in by the editor (postMessage) instead of the saved art.
 *
 * Rendering is up to the game: DOM, canvas, Phaser... APGE only delivers state.
 */
(function () {
    'use strict';

    function storageGet(store, key) {
        try { return window[store].getItem(key); } catch (e) { return null; }
    }
    function storageSet(store, key, value) {
        try { window[store].setItem(key, value); } catch (e) { /* private mode */ }
    }
    function storageRemove(store, key) {
        try { window[store].removeItem(key); } catch (e) { /* private mode */ }
    }

    function el(target) {
        return typeof target === 'string' ? document.getElementById(target) || document.querySelector(target) : target;
    }

    function escapeHtml(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }

    function Client(options) {
        const config = Object.assign({}, window.APGE_CONFIG || {}, options || {});
        this.role = config.role || 'player';
        this.hostKey = config.hostKey || null;
        this.view = null;
        this.me = null;
        this.phase = null;
        this.connected = false;
        this._handlers = {};
        this._timerToken = null;
        this._timerReceived = 0;
        this.phaseStarted = Date.now();
        this.preview = config.preview || null;
        const self = this;

        if (this.preview) {
            this.socket = null;
            this.name = null;
            startPreview(this);
            return;
        }

        // Per-browser token: lets the server tell "me again" from "someone else with my name"
        this.token = storageGet('localStorage', 'apge.token');
        if (!this.token) {
            this.token = Math.random().toString(36).slice(2) + Date.now().toString(36);
            storageSet('localStorage', 'apge.token', this.token);
        }
        // Name survives a page reload in the same tab, so a refresh rejoins automatically
        this.name = this.role === 'player' ? storageGet('sessionStorage', 'apge.name') : null;

        const socket = this.socket = io({
            reconnection: true,
            reconnectionAttempts: Infinity,
            reconnectionDelay: 500,
            reconnectionDelayMax: 3000,
            timeout: 20000
        });

        socket.on('connect', function () {
            self.connected = true;
            self._badge();
            self._emit('connection', true);
            if (self.role === 'host') {
                socket.emit('apge:host', {key: self.hostKey});
            } else if (self.name) {
                self.join(self.name, {auto: true});
            }
        });
        socket.on('disconnect', function () {
            self.connected = false;
            self._badge();
            self._emit('connection', false);
        });
        socket.on('apge:state', function (view) { self._onState(view); });
        socket.on('apge:message', function (m) { self._emit('message', m.event, m.data); self._emit('message:' + m.event, m.data); });
        socket.on('apge:relay', function (r) { self._emit('relay', r); });
        socket.on('apge:replaced', function () {
            self.me = null;
            self._emit('replaced');
            self.toast('This game was opened in another tab.');
        });
        socket.on('apge:kicked', function (info) {
            const reason = (info && info.reason) || 'kick';
            self.me = null;
            self.name = null;
            storageRemove('sessionStorage', 'apge.name');
            self._emit('kicked', reason);
            self.toast(reason === 'quit' ? 'The host ended the game. Join again to play!'
                                         : 'The host removed you from the lobby.');
        });

        // Phones that wake from sleep should reconnect right away
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && !socket.connected) socket.connect();
        });
    }

    Client.prototype.on = function (event, fn) {
        (this._handlers[event] = this._handlers[event] || []).push(fn);
        // Late subscribers get the current state immediately
        if (event === 'state' && this.view) fn(this.view);
        return this;
    };

    Client.prototype._emit = function (event) {
        const args = Array.prototype.slice.call(arguments, 1);
        (this._handlers[event] || []).forEach(function (fn) {
            try { fn.apply(null, args); } catch (e) { console.error('APGE handler for "' + event + '" failed:', e); }
        });
    };

    Client.prototype._onState = function (view) {
        const previousPhase = this.phase;
        this.view = view;
        this.me = view.me || null;
        this.phase = view.phase;
        // The server sends a fresh `remaining` with every view; count down locally from there
        this._timerToken = view.timer ? view.timer.token : null;
        this._timerReceived = Date.now();
        if (previousPhase !== view.phase) {
            this.phaseStarted = Date.now() - (this.preview ? (this.preview.elapsed || 0) * 1000 : 0);
            this._emit('phase', view.phase, view, previousPhase);
        }
        this._emit('state', view);
    };

    function ackPromise(socket, event, payload) {
        if (!socket) return Promise.resolve({ok: false, error: 'This is a Developer Mode preview.'});
        return new Promise(function (resolve) {
            let done = false;
            const timer = setTimeout(function () {
                if (!done) { done = true; resolve({ok: false, error: 'No answer from the game - check your connection.'}); }
            }, 8000);
            socket.emit(event, payload, function (ack) {
                if (done) return;
                done = true;
                clearTimeout(timer);
                resolve(ack || {ok: true});
            });
        });
    }

    // ---- players ------------------------------------------------------------

    Client.prototype.join = function (name, opts) {
        const self = this;
        opts = opts || {};
        return ackPromise(this.socket, 'apge:join', {name: name, token: this.token}).then(function (ack) {
            if (ack.ok) {
                self.name = ack.player.name;
                storageSet('sessionStorage', 'apge.name', self.name);
                self._emit('joined', ack.player);
            } else {
                // Don't keep retrying a name the server refused
                self.name = null;
                storageRemove('sessionStorage', 'apge.name');
                self._emit('join_error', ack.error);
                if (!opts.auto) self.toast(ack.error);
            }
            return ack;
        });
    };

    /** Send a game action. Resolves with the ack; shows the error as a toast unless {silent: true}. */
    Client.prototype.action = function (name, data, opts) {
        const self = this;
        opts = opts || {};
        return ackPromise(this.socket, 'apge:action', {name: name, data: data || {}}).then(function (ack) {
            if (!ack.ok && !opts.silent) self.toast(ack.error);
            return ack;
        });
    };

    Client.prototype.ready = function () {
        return ackPromise(this.socket, 'apge:ready', {});
    };

    /** Fire-and-forget input straight to the host screen (realtime controller games). */
    Client.prototype.relay = function (event, data) {
        if (this.socket) this.socket.volatile.emit('apge:relay', {event: event, data: data});
    };

    // ---- host -----------------------------------------------------------------

    Client.prototype._host = function (command, extra) {
        const self = this;
        return ackPromise(this.socket, 'apge:host_command', Object.assign({command: command}, extra || {}))
            .then(function (ack) { if (!ack.ok) self.toast(ack.error); return ack; });
    };
    Client.prototype.start = function () { return this._host('start'); };
    Client.prototype.skip = function () { return this._host('skip'); };
    Client.prototype.reset = function () { return this._host('reset'); };
    /** End the session: back to the lobby and every player is removed (they rejoin to play again). */
    Client.prototype.quit = function () { return this._host('quit'); };
    Client.prototype.kick = function (playerId) { return this._host('kick', {player_id: playerId}); };
    Client.prototype.isHost = function () { return !!(this.view && this.view.role === 'host'); };
    /** True on the phone of the player the game lets send its vip_commands (e.g. Start). */
    Client.prototype.isVip = function () {
        return !!(this.view && this.me && this.view.vip_id && this.view.vip_id === this.me.id);
    };

    // ---- timers -----------------------------------------------------------------

    /** Seconds left on the current phase timer (null if none), counting down locally between updates. */
    Client.prototype.timeRemaining = function () {
        const t = this.view && this.view.timer;
        if (!t) return null;
        return Math.max(0, t.remaining - (Date.now() - this._timerReceived) / 1000);
    };

    /**
     * Keep an element showing the countdown. Optional `bar` element gets its width set.
     * opts.urgentAt: seconds left when the element gets `apge-urgent` (default 5), or a
     *   function(view) returning that number, or null for "never urgent in this view".
     * opts.onUrgent(view): called once per timer as it counts down past urgentAt (not for a
     *   timer that was already that low when this screen first saw it, e.g. after a reload).
     */
    Client.prototype.bindCountdown = function (target, bar, opts) {
        const self = this;
        const node = el(target), fill = bar ? el(bar) : null;
        opts = opts || {};
        const urgentAt = opts.urgentAt === undefined ? 5 : opts.urgentAt;
        let armed = null, fired = null;   // timer tokens: seen above the threshold / callback done
        setInterval(function () {
            const left = self.timeRemaining();
            const view = self.view;
            const at = typeof urgentAt === 'function' ? (view ? urgentAt(view) : null) : urgentAt;
            const token = self._timerToken;
            const urgent = left != null && at != null && left <= at;
            if (left != null && at != null && left > at) armed = token;
            if (urgent && armed === token && fired !== token) {
                fired = token;
                if (opts.onUrgent) {
                    try { opts.onUrgent(view); } catch (e) { console.error('onUrgent failed:', e); }
                }
            }
            if (node) {
                node.textContent = left == null ? '' : Math.ceil(left) + 's';
                node.classList.toggle('apge-urgent', urgent);
            }
            if (fill) {
                const t = self.view && self.view.timer;
                fill.style.width = t ? (100 * left / t.duration) + '%' : '0%';
            }
        }, 200);
        return this;
    };

    // ---- screens ----------------------------------------------------------------

    /**
     * Show one element per phase: {phase: elementId, ..., '*': fallbackId}.
     * Optional 'join' entry is shown to players who haven't joined yet.
     */
    Client.prototype.screens = function (map) {
        const self = this;
        const ids = Object.keys(map).map(function (k) { return map[k]; });
        function show(view) {
            let id = map[view.phase] || map['*'];
            if (self.role === 'player' && !view.me && map.join) id = map.join;
            ids.forEach(function (other) {
                const node = el(other);
                if (node) node.classList.toggle('apge-active', other === id);
            });
        }
        this.on('state', show);
        return this;
    };

    // ---- small built-in UI --------------------------------------------------------

    Client.prototype.toast = function (message) {
        if (!message) return;
        let box = document.getElementById('apge-toast');
        if (!box) {
            box = document.createElement('div');
            box.id = 'apge-toast';
            document.body.appendChild(box);
        }
        box.textContent = message;
        box.classList.add('apge-show');
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(function () { box.classList.remove('apge-show'); }, 3500);
    };

    Client.prototype._badge = function () {
        let badge = document.getElementById('apge-connection');
        if (!badge) {
            badge = document.createElement('div');
            badge.id = 'apge-connection';
            badge.textContent = 'Reconnecting...';
            document.body.appendChild(badge);
        }
        badge.classList.toggle('apge-show', !this.connected);
    };

    /** A name box + Join button. Shows join errors underneath. */
    Client.prototype.mountJoinForm = function (target, opts) {
        const self = this;
        opts = opts || {};
        const root = el(target);
        root.innerHTML =
            '<form class="apge-join">' +
            '<input class="apge-input" maxlength="24" autocomplete="off" placeholder="' + escapeHtml(opts.placeholder || 'Your name') + '">' +
            '<button class="apge-button" type="submit">' + escapeHtml(opts.button || 'Join') + '</button>' +
            '<p class="apge-error"></p></form>';
        const input = root.querySelector('input'), error = root.querySelector('.apge-error');
        input.value = this.name || '';
        // The game decides the longest name (Game.max_name)
        this.on('state', function (view) { if (view.max_name) input.maxLength = view.max_name; });
        root.querySelector('form').addEventListener('submit', function (e) {
            e.preventDefault();
            const name = input.value.trim();
            if (!name) return;
            error.textContent = '';
            self.join(name, {auto: true}).then(function (ack) { if (!ack.ok) error.textContent = ack.error; });
        });
        return this;
    };

    /**
     * Host lobby/controls panel: QR code, join URL, player list, Start / Skip / New Game.
     * Games with their own art can skip this and call start()/skip()/reset() directly.
     */
    Client.prototype.mountHostPanel = function (target) {
        const self = this;
        const root = el(target);
        root.innerHTML =
            '<div class="apge-panel">' +
            '<div class="apge-qr"><img src="/apge-qr.png" alt="Scan to join"><p class="apge-url"></p></div>' +
            '<ul class="apge-players"></ul>' +
            '<div class="apge-controls">' +
            '<button class="apge-button" data-cmd="start">Start</button>' +
            '<button class="apge-button apge-secondary" data-cmd="skip">Skip waiting players</button>' +
            '<button class="apge-button" data-cmd="reset">New game</button>' +
            '<button class="apge-button apge-secondary" data-cmd="quit">End game</button>' +
            '</div><p class="apge-note"></p></div>';
        fetch('/apge-info').then(function (r) { return r.json(); }).then(function (info) {
            root.querySelector('.apge-url').textContent = info.player_url;
        }).catch(function () { /* offline host page - URL just stays blank */ });

        root.querySelectorAll('[data-cmd]').forEach(function (b) {
            b.addEventListener('click', function () {
                const cmd = b.getAttribute('data-cmd');
                if (cmd === 'skip' && !confirm('Skip everyone who hasn\'t finished this step?')) return;
                if (cmd === 'reset' && !confirm('Start a new game with the same players?')) return;
                if (cmd === 'quit' && !confirm('End the game? Everyone will need to join again.')) return;
                self[cmd]();
            });
        });

        this.on('state', function (view) {
            const lobby = view.phase === 'lobby', over = view.phase === (view.end_phase || 'game_over');
            const host = view.role === 'host';
            root.querySelector('.apge-qr').style.display = lobby ? '' : 'none';
            root.querySelector('[data-cmd=start]').style.display = host && lobby ? '' : 'none';
            root.querySelector('[data-cmd=start]').disabled = view.players.length < view.min_players;
            root.querySelector('[data-cmd=skip]').style.display = host && !lobby && !over ? '' : 'none';
            root.querySelector('[data-cmd=reset]').style.display = host && over ? '' : 'none';
            root.querySelector('[data-cmd=quit]').style.display = host && over ? '' : 'none';
            root.querySelector('.apge-note').textContent = host ? '' :
                'View only - host controls are on the computer running the game.';
            root.querySelector('.apge-players').innerHTML = view.players.map(function (p) {
                const kick = host && lobby ? ' <button class="apge-kick" data-id="' + p.id + '" title="Remove">✕</button>' : '';
                return '<li class="' + (p.connected ? '' : 'apge-offline') + '">' + escapeHtml(p.name) +
                    (p.connected ? '' : ' (disconnected)') + (p.ready ? ' ✓' : '') + kick + '</li>';
            }).join('') || '<li class="apge-offline">Waiting for players...</li>';
            root.querySelectorAll('.apge-kick').forEach(function (b) {
                b.addEventListener('click', function () { self.kick(b.getAttribute('data-id')); });
            });
        });
        return this;
    };

    // ---- Developer Mode previews -----------------------------------------------------

    function startPreview(client) {
        const preview = client.preview;
        function begin() {
            if (preview.error) {
                const box = document.createElement('pre');
                box.className = 'apge-preview-error';
                box.textContent = 'This sample screen failed to build:\n\n' + preview.error;
                document.body.appendChild(box);
                return;
            }
            client._onState(preview.view);
        }
        // Let the page's own scripts subscribe first, as they would before a socket connects
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', begin);
        else setTimeout(begin, 0);
        // The art editor sends drafts to its preview frames; show them in place of saved art
        art.onChange(function () { if (client.view) client._onState(client.view); });
    }

    // ---- game art -------------------------------------------------------------------------

    const art = (function () {
        const entries = (window.APGE_CONFIG || {}).art || {};
        const images = {};
        const listeners = [];

        function info(id) { return entries[id] || null; }
        function url(id) { const a = entries[id]; return a && a.url ? a.url : null; }

        /** A loaded <img> for canvas drawing, or null while it loads (canvas loops redraw). */
        function image(id) {
            const src = url(id);
            if (!src) return null;
            let img = images[id];
            if (!img || img.dataset.src !== src) {
                img = images[id] = new Image();
                img.dataset.src = src;
                img.onerror = function () { img.dataset.failed = '1'; };
                img.src = src;
            }
            return img.complete && img.naturalWidth ? img : null;
        }

        function failed(id) { const img = images[id]; return !!(img && img.dataset.failed); }

        /**
         * Draw an asset inside a box, keeping its shape. opts.fit: 'contain' (default) or
         * 'fill' (stretch to the box); opts.align: 'center' (default), 'bottom' or 'top'.
         * Returns false when there's nothing to draw, so the caller can draw a placeholder.
         */
        function draw(ctx, id, x, y, w, h, opts) {
            opts = opts || {};
            const img = image(id), a = entries[id];
            // Still loading: draw nothing rather than flash the placeholder. Broken: placeholder
            if (!img) return !!url(id) && !failed(id);
            let dw = w, dh = h;
            if (opts.fit !== 'fill') {
                const s = Math.min(w / img.naturalWidth, h / img.naturalHeight);
                dw = img.naturalWidth * s;
                dh = img.naturalHeight * s;
            }
            const dx = x + (w - dw) / 2;
            const dy = opts.align === 'bottom' ? y + h - dh : opts.align === 'top' ? y : y + (h - dh) / 2;
            const smooth = ctx.imageSmoothingEnabled;
            ctx.imageSmoothingEnabled = !a.pixelated;
            ctx.drawImage(img, dx, dy, dw, dh);
            ctx.imageSmoothingEnabled = smooth;
            return true;
        }

        function onChange(fn) { listeners.push(fn); }

        window.addEventListener('message', function (e) {
            const m = e.data;
            if (e.origin !== window.location.origin || !m || m.apge !== 'art' || !entries[m.id]) return;
            entries[m.id] = Object.assign({}, entries[m.id], m.info);
            listeners.forEach(function (fn) { try { fn(m.id); } catch (err) { console.error(err); } });
        });

        return {has: function (id) { return !!url(id); }, url: url, info: info, image: image,
                draw: draw, onChange: onChange};
    })();

    // ---- sound ----------------------------------------------------------------------
    // Browsers keep a page silent until someone clicks, taps or presses a key on it, so the
    // first of those switches sound on. A host screen nobody has touched stays silent:
    // check APGE.sound.allowed() and ask for a click.

    const sound = (function () {
        let audio = null;

        function unlock() {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!audio && Ctx) audio = new Ctx();
            if (audio && audio.state === 'suspended') audio.resume();
        }
        ['pointerdown', 'keydown', 'touchstart'].forEach(function (type) {
            document.addEventListener(type, unlock, {capture: true, passive: true});
        });

        /** True once the browser lets this page make sound. */
        function allowed() { return !!audio && audio.state === 'running'; }

        /** A short tone. opts: freq (Hz, 880), ms (250), volume (0-1, 0.3). False if silent. */
        function beep(opts) {
            opts = opts || {};
            if (!allowed()) return false;
            const now = audio.currentTime, secs = (opts.ms || 250) / 1000;
            const osc = audio.createOscillator(), gain = audio.createGain();
            osc.type = 'square';
            osc.frequency.value = opts.freq || 880;
            gain.gain.setValueAtTime(opts.volume === undefined ? 0.3 : opts.volume, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + secs);   // no click at the end
            osc.connect(gain).connect(audio.destination);
            osc.start(now);
            osc.stop(now + secs);
            return true;
        }

        return {allowed: allowed, beep: beep};
    })();

    window.APGE = {
        connect: function (options) { return new Client(options); },
        escapeHtml: escapeHtml,
        art: art,
        sound: sound,
        version: (window.APGE_CONFIG || {}).apgeVersion
    };
})();
