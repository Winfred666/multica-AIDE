"""Native installation SOP and host controller commands."""
import argparse
import json
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'multica_aide'

from .standalone import SKILLS, begin, load_config, read, search
from .worktrees import sandbox
from .viewer import serve


def client_main():
    wrapper = Path.cwd() / '.agents/skills/multica-aide-cli/wrapper.py'
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
    dev = commands.add_parser('develop', help='run a development command inside its prepared bubblewrap sandbox')
    dev.add_argument('--workdir', type=Path, required=True, help='the validator client worktree')
    dev.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'develop':
        argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
        if not argv:
            parser.error('develop requires a command after --')
        binding = read(args.workdir / '.aide-development.json')
        command = sandbox(binding['policy'], binding['candidate'], argv)
        os.execv(command[0], command)
    config = load_config(args.config)
    if args.command == 'begin':
        if args.max_rounds < 0:
            parser.error('--max-rounds must be nonnegative')
        print(json.dumps(begin(config, args.max_rounds), allow_nan=False))
    else:
        serve(search(config), config['port'])


if __name__ == '__main__':
    main()
