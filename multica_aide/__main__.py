"""Command-line access to tree contexts and native Multica campaigns."""
import argparse
import json
import os
from pathlib import Path

from .standalone import begin, create_dev, install, load_config, search, submit
from .viewer import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.yaml'))
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('install', help='preview configuration; confirm it before creating native resources')
    setup.add_argument('--parent-issue', default=os.environ.get('MULTICA_ISSUE_ID'))
    setup.add_argument('--confirm-config', action='store_true')
    run = commands.add_parser('begin', help='run the foreground client loop and optional background tree viewer')
    run.add_argument('--max-rounds', type=int, default=0, help='completed rounds per client; 0 runs until interrupted')
    commands.add_parser('serve', help='host only the tree widget on the configured port')
    context = commands.add_parser('context', help='reserve or recover one client round as JSON')
    context.add_argument('--session-id', required=True)
    send = commands.add_parser('submit', help='request official host scoring of the current solution checkout')
    send.add_argument('--id', required=True)
    send.add_argument('--title', required=True)
    send.add_argument('--description', required=True)
    send.add_argument('--review')
    dev = commands.add_parser('dev', help='delegate a code child with the client checkout and hidden-data policy')
    dev.add_argument('--agent-id', required=True)
    dev.add_argument('--title', required=True)
    dev.add_argument('--description-file', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'submit':
        result = submit(args.id, args.title, args.description, args.review)
    elif args.command == 'dev':
        result = create_dev(args.agent_id, args.title, args.description_file)
    else:
        config = load_config(args.config)
        if args.command == 'install':
            if args.confirm_config and not args.parent_issue:
                parser.error('--parent-issue or MULTICA_ISSUE_ID is required')
            result = install(config, args.parent_issue, args.confirm_config)
        elif args.command == 'begin':
            if args.max_rounds < 0:
                parser.error('--max-rounds must be nonnegative')
            result = begin(config, args.max_rounds)
        elif args.command == 'context':
            result = search(config).next_context(args.session_id)
        else:
            if not config.get('port'):
                parser.error('serve requires a configured port')
            serve(search(config), config['port'])
            return
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
