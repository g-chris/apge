"""
Virtual-user tests: bots play full games of __GAME_TITLE__ with timers sped up 20x.

    python tests/test_bots.py            # full matrix, 3 seeds per scenario
    python tests/test_bots.py --quick    # 1 seed

Update MyBot.decide() and the invariant as your game grows.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from apge.testing import Bot, run_matrix, standard_scenarios  # noqa: E402

from game import HotTake  # noqa: E402


class HotTakeBot(Bot):
    def decide(self, view):
        me, data = view['me'], view['data']
        if view['phase'] == 'write' and not me['answered']:
            return [('answer', {'text': f"{self.name}'s take #{self.rng.randint(1, 99)}"})]
        if view['phase'] == 'vote' and 'vote' not in me['private']:
            options = [a['id'] for a in data.get('answers', []) if a['id'] != me['private'].get('answer_id')]
            if options:
                return [('vote', {'answer': self.rng.choice(options)})]
        return None


def invariant(game):
    for p in game.players.values():
        assert p.public.get('score', 0) >= 0, f'{p.name} has a negative score'
    if game.phase == 'vote':
        assert len(game.public['answers']) <= len(game.players), 'more answers than players'


if __name__ == '__main__':
    seeds = 1 if '--quick' in sys.argv else 3
    scenarios = standard_scenarios(HotTakeBot, players=4, ghost_phases=['write', 'vote'])
    ok = run_matrix(HotTake, scenarios, seeds=seeds, invariant=invariant)
    sys.exit(0 if ok else 1)
