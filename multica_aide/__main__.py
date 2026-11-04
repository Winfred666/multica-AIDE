"""Native installation SOP and host controller commands."""
import argparse
import json
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'multica_aide'

from .standalone import SKILLS, begin, load_config, search
from .viewer import serve


def client_main():
    wrapper = SKILLS / 'multica-aide-cli' / 'wrapper.py'
    os.execv(sys.executable, [sys.executable, str(wrapper), *sys.argv[1:]])


def main():
    if sys.argv[1:2] == ['install']:
        wrapper = SKILLS / 'multica-aide-install' / 'wrapper.py'
        os.execv(sys.executable, [sys.executable, str(wrapper), *sys.argv[2:]])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.local.yaml'))
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('begin', help='host the native SOP clients and tree board')
    run.add_argument('--max-rounds', type=int, default=0)
    commands.add_parser('serve', help='host the read-only tree board')
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == 'begin':
        if args.max_rounds < 0:
            parser.error('--max-rounds must be nonnegative')
        print(json.dumps(begin(config, args.max_rounds), allow_nan=False))
    else:
        serve(search(config), config['port'])


if __name__ == '__main__':
    main()
