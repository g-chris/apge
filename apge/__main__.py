"""
APGE command line.

    python -m apge new my-game --title "My Game"    # start a new game from the starter template
    python -m apge version
"""
import argparse
import os
import re
import shutil
import sys

from . import __version__

SCAFFOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scaffold')
# Dot-files can't be relied on inside a package, so the template stores them without the dot
RENAMES = {'gitignore': '.gitignore', 'github': '.github'}
TEXT_SUFFIXES = ('.py', '.html', '.js', '.css', '.md', '.txt', '.yml', '.spec', 'gitignore')


def new_game(target, title):
    if os.path.exists(target) and os.listdir(target):
        sys.exit(f'{target} already exists and is not empty.')
    slug = re.sub(r'[^A-Za-z0-9]+', '', title.title()) or 'Game'
    replacements = {'__GAME_TITLE__': title, '__GAME_SLUG__': slug, '__APGE_VERSION__': __version__}

    for root, dirs, files in os.walk(SCAFFOLD):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        rel = os.path.relpath(root, SCAFFOLD)
        parts = [] if rel == '.' else [RENAMES.get(p, p) for p in rel.split(os.sep)]
        out_dir = os.path.join(target, *parts)
        os.makedirs(out_dir, exist_ok=True)
        for name in files:
            src = os.path.join(root, name)
            dst = os.path.join(out_dir, RENAMES.get(name, name))
            if name.endswith(TEXT_SUFFIXES):
                with open(src, encoding='utf-8') as f:
                    text = f.read()
                for key, value in replacements.items():
                    text = text.replace(key, value)
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write(text)
            else:
                shutil.copy2(src, dst)

    print(f'Created {title} in {target}\n')
    print('Next steps:')
    print(f'  cd {target}')
    print('  python3 -m venv venv && source venv/bin/activate')
    print('  pip install -r requirements.txt')
    print('  python app.py')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m apge', description='Async Party Game Engine')
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('new', help='create a new game from the starter template')
    new.add_argument('directory')
    new.add_argument('--title', help='game title (default: from the directory name)')
    sub.add_parser('version', help='print the APGE version')
    args = parser.parse_args(argv)

    if args.command == 'version':
        print(__version__)
    elif args.command == 'new':
        title = args.title or os.path.basename(os.path.abspath(args.directory)).replace('-', ' ').replace('_', ' ').title()
        new_game(args.directory, title)


if __name__ == '__main__':
    main()
