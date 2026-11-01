"""Run __GAME_TITLE__: python app.py"""
import os

from apge import PartyServer

from game import HotTake

if __name__ == '__main__':
    PartyServer(HotTake(), base_dir=os.path.dirname(os.path.abspath(__file__))).run()
