# APGE: Async Party Game Engine

APGE is the reusable core for party games where **one screen hosts** (a TV or laptop) and **everyone plays on their phone** over the local network. Players don't install an app or create accounts, and you don't need internet.

It started as the networking layer of [Hollywood Moguls](https://github.com/g-chris/showbiz). Every rule below exists because a bot test or a real game night broke without it.

You write the rules and the art. APGE handles:

- **Connections:** joining by name, the QR code, automatic reconnects when a phone sleeps, refused duplicate names, and no late joiners.
- **Flow:** phases, timers, "everyone pressed Continue" screens, and a host Skip button for anyone who walks away.
- **Fairness:** a player action only counts in the phases it's declared for, and nothing races. Each player sees only their own private state.
- **Shipping:** single-file Windows/macOS/Linux builds and a GitHub release workflow.
- **Testing:** bots that play whole games while idling, double-tapping, dropping connection and quitting.

## Start a new game

```bash
pip install "apge[testing] @ git+https://github.com/g-chris/apge@v0.1.1"
python -m apge new my-game --title "My Game"
cd my-game
python app.py                 # host screen opens at http://localhost:8080
python tests/test_bots.py     # bots play the game
```

The starter, *Hot Take*, is a small but complete game: answer a prompt, vote anonymously, see the scores, play 3 rounds. Change it into your game.

Every new game also gets `CONVENTIONS.txt`: how we handle changelogs, versions, releases, pull requests and reviews, reusable code, and testing. Fill in its GAME DESIGN section from your design doc, and give it to Claude at the start of each session.

## Writing a game

```python
from apge import Game, Reject, action, phase, on_timeout, on_all_ready

class MyGame(Game):
    title = 'My Game'
    min_players = 2

    def on_start(self):                      # host pressed Start
        for p in self.players.values():
            p.public['score'] = 0
        self.goto('answer')

    @phase('answer', timer=60)               # entering the phase starts its timer
    def enter_answer(self):
        self.public['prompt'] = 'Name a fish'

    @action('submit', phases='answer')       # only accepted during 'answer'
    def submit(self, player, data):
        if not data.get('text'):
            raise Reject('Type something!')  # shown to that player as a message
        player.private['answer'] = data['text']
        if self.everyone(lambda p: 'answer' in p.private, include_disconnected=False):
            self.goto('results')

    @on_timeout('answer')                    # also what the host's Skip button does
    def answer_timeout(self):
        self.goto('results')

    @phase('results', timer=20, ready=True)  # a "Continue" screen
    def enter_results(self): ...

    @on_all_ready('results')                 # everyone pressed Continue, or the timer ran out
    def results_done(self):
        self.goto('game_over')               # Game.end_phase: shows "New game" on the host
```

```python
# app.py
import os
from apge import PartyServer
from game import MyGame

PartyServer(MyGame(), base_dir=os.path.dirname(os.path.abspath(__file__))).run()
```

### Rules the engine enforces

| You get | Because Showbiz taught us |
|---|---|
| Players are keyed by a stable `player.id`; socket ids never reach game code | Remapping socket ids on reconnect missed one list and froze the game |
| An action only runs in its declared `phases`; anything else gets "Too late - the game has moved on" | A late tap re-ran a transition and restarted a season |
| Actions, timers, connects and host commands run one at a time, then each screen gets one update | Timer threads raced player clicks |
| Each timer has a token, so a superseded timer firing does nothing | An old timer resolved a new bidding war |
| Host **Skip** behaves as if the timer ran out (override with `@on_skip`), so no phase can soft-lock | A player who left during packaging froze the game |
| Continue screens don't wait for disconnected players | One sleeping phone held up everyone |
| A name that's in use is refused unless it's the same browser (per-browser token) | A second "Ann" silently took over the first Ann's studio |
| Only the computer running the game gets host controls; other screens opening `/` get a read-only view | Any phone could press Start |
| The socket.io client is bundled | The game didn't load on Wi-Fi without internet |

### State and views

- `self.public` is game state everyone sees. `player.public` is per-player and visible to everyone (e.g. the score). `player.private` is visible to that player only. Anything else you store on `self` is server-only (e.g. who wrote which anonymous answer).
- Each phone receives `player_view(player)`, which is the public state plus `me` (with `me.private`). The host screen receives `host_view()`. Override either to add or hide data.
- `self.send(player, 'event', data)` and `self.broadcast(...)` send one-off messages (sounds, toasts), received in JS as `apge.on('message:event', ...)`.

### Client (`apge.js`)

Templates put `{{ apge_head() }}` in `<head>`. The host page is `templates/host.html` at `/`, and the phone page is `templates/player.html` at `/player`.

```js
const apge = APGE.connect();                     // role and host key come from the page
apge.on('state', view => render(view));          // view.phase, view.data, view.players, view.me, view.timer
apge.on('phase', (phase, view, previous) => playTransition(phase));
apge.screens({join: 'join', lobby: 'lobby', answer: 'answer', '*': 'wait'});  // toggles .apge-active
apge.action('submit', {text}).then(ack => ...);  // errors are shown as a toast automatically
apge.ready();                                    // Continue
apge.timeRemaining(); apge.bindCountdown('timer-el');
apge.mountJoinForm('el'); apge.mountHostPanel('el');   // optional ready-made UI
apge.start(); apge.skip(); apge.reset(); apge.kick(id); // host only
apge.relay('tilt', {x, y});                      // realtime: phone -> host screen, no server state
apge.on('relay', ({player_id, event, data}) => ...);    // on the host page
```

Rendering is entirely yours: DOM, canvas, Phaser or anything else. APGE only delivers state. For realtime "phone as controller" games, run the action on the host page and stream input with `relay()`. Keep the server-side phases for the lobby, rounds and scoring.

## Testing

```python
from apge.testing import Bot, run_matrix, standard_scenarios

class MyBot(Bot):
    def decide(self, view):                 # return the actions to take for this state
        if view['phase'] == 'answer' and 'answer' not in view['me']['private']:
            return [('submit', {'text': 'cod'})]

run_matrix(MyGame, standard_scenarios(MyBot, players=4, ghost_phases=['answer']),
           seeds=3, invariant=lambda game: ...)
```

The standard scenarios are: eager, solo, idle players, all idle, slow, double-tap, chaos (replaying old actions out of phase), flaky (reconnecting), all flaky, a second game after New Game, and a "ghost" quitting in each listed phase (rescued by the host pressing Skip). Timers run 20× faster.

A run fails on any of these:
- a stall (no state change for 20 s)
- a server error
- a bot left without a player view
- a failed `invariant(game)`

Rejected actions are expected and don't fail a run.

The engine's own tests (`python -m unittest discover tests`) include checks that the harness really does catch stalls, crashes and invariant failures.

## Shipping a game

The starter includes `build.spec` and `.github/workflows/release.yml`. Push a `v1.0.0` tag and GitHub Actions will:
1. run the bot tests,
2. build single-file executables for Windows, macOS (Apple Silicon) and Linux,
3. smoke-test each one,
4. attach them to a release.

Link `releases/latest/download/<file>` from your README.

| Environment variable | Effect |
|---|---|
| `APGE_PORT` | Port (default 8080) |
| `APGE_NO_BROWSER=1` | Don't open the host page on startup |
| `APGE_REMOTE_HOST=1` | Give host controls to any device that opens `/` (e.g. a smart TV browser) |
| `APGE_TIME_SCALE` | Multiply all timers (tests use 0.05) |

## Versioning

Games pin an exact tag (`apge @ git+https://github.com/g-chris/apge@v0.1.1`). Improve APGE while building a game, tag a new version, and bump the game's pin when you're ready. Breaking changes bump the minor version while APGE is 0.x, and [CHANGELOG.md](CHANGELOG.md) says what to change.

## Licence

MIT - see [LICENSE](LICENSE).
