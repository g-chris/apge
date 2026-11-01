// Phone screen for __GAME_TITLE__
const apge = APGE.connect();
const esc = APGE.escapeHtml;

apge.screens({join: 'join', lobby: 'lobby', write: 'write', vote: 'vote',
              reveal: 'reveal', game_over: 'game_over', '*': 'lobby'});
apge.mountJoinForm('join-form', {placeholder: 'Your name', button: 'Join'});
['write-timer', 'vote-timer', 'reveal-timer'].forEach(id => apge.bindCountdown(id));

document.getElementById('answer-form').addEventListener('submit', e => {
    e.preventDefault();
    const input = document.getElementById('answer-text');
    apge.action('answer', {text: input.value}).then(ack => { if (ack.ok) input.value = ''; });
});
document.getElementById('continue').addEventListener('click', () => apge.ready());

apge.on('state', view => {
    const me = view.me;
    const data = view.data;
    document.getElementById('who').textContent = me ? `${me.name} · ${me.score || 0} pts` : '';
    document.querySelectorAll('[data-prompt]').forEach(n => { n.textContent = data.prompt || ''; });
    if (!me) return;

    if (view.phase === 'write') {
        const answered = me.answered;
        document.getElementById('answer-form').style.display = answered ? 'none' : '';
        document.getElementById('answer-sent').style.display = answered ? '' : 'none';
    }

    if (view.phase === 'vote') {
        const mine = me.private.answer_id;
        const voted = me.private.vote;
        document.getElementById('vote-options').innerHTML = (data.answers || []).map(a => {
            const own = a.id === mine;
            const chosen = a.id === voted;
            return `<button class="apge-button option ${chosen ? 'chosen' : ''}" data-id="${a.id}"
                     ${own || voted !== undefined ? 'disabled' : ''}>${esc(a.text)}${own ? ' (yours)' : ''}</button>`;
        }).join('');
        document.querySelectorAll('#vote-options [data-id]').forEach(b => {
            b.addEventListener('click', () => apge.action('vote', {answer: Number(b.dataset.id)}));
        });
        const canVote = (data.answers || []).some(a => a.id !== mine);
        document.getElementById('vote-sent').textContent =
            voted !== undefined ? 'Vote in! Waiting for everyone else...' :
            canVote ? '' : 'Nothing to vote for this time - sit back.';
    }

    if (view.phase === 'reveal') {
        document.getElementById('reveal-results').innerHTML = (data.results || []).map(r =>
            `<li><strong>${esc(r.text)}</strong> — ${esc(r.author)} <span>${r.votes} vote${r.votes === 1 ? '' : 's'}</span></li>`).join('');
        const btn = document.getElementById('continue');
        btn.disabled = me.ready;
        btn.textContent = me.ready ? 'Waiting for others...' : 'Continue';
    }

    if (view.phase === 'game_over') {
        document.getElementById('final-results').innerHTML = (data.standings || []).map(s =>
            `<li>${esc(s.name)} <span>${s.score} pts</span></li>`).join('');
    }
});
