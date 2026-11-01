// Host (TV) screen for __GAME_TITLE__
const apge = APGE.connect();
const esc = APGE.escapeHtml;

apge.mountHostPanel('panel');
apge.bindCountdown('timer');

apge.on('state', view => {
    const data = view.data;
    const board = document.getElementById('board');
    const prompt = document.getElementById('prompt');
    prompt.textContent = ['write', 'vote'].includes(view.phase) ? data.prompt : '';

    if (view.phase === 'lobby') {
        board.innerHTML = '<p>Scan the code to join, then press Start.</p>';
    } else if (view.phase === 'write') {
        const done = view.players.filter(p => p.answered).length;
        board.innerHTML = `<p>Round ${data.round} of ${data.rounds} · ${done}/${view.players.length} answered</p>`;
    } else if (view.phase === 'vote') {
        board.innerHTML = '<ul class="answers">' + data.answers.map(a => `<li>${esc(a.text)}</li>`).join('') + '</ul>';
    } else if (view.phase === 'reveal') {
        board.innerHTML = '<ol class="results">' + data.results.map(r =>
            `<li><strong>${esc(r.text)}</strong> — ${esc(r.author)} <span>${r.votes} vote${r.votes === 1 ? '' : 's'}</span></li>`).join('') + '</ol>';
    } else if (view.phase === 'game_over') {
        board.innerHTML = '<h2>Final scores</h2><ol class="results">' + data.standings.map(s =>
            `<li>${esc(s.name)} <span>${s.score} pts</span></li>`).join('') + '</ol>';
    }
});
