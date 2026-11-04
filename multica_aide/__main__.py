"""Command-line access to tree contexts and native Multica campaigns."""
import argparse
import json
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'multica_aide'

from .standalone import begin, create_dev, describe, install, load_config, native_client, request_score, search, submit
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
    register = commands.add_parser('register', aliases=['describe'], help='register a node description before grading')
    register.add_argument('--id', required=True)
    register.add_argument('--title', required=True)
    register.add_argument('--description', required=True)
    register.add_argument('--session-id')
    register.add_argument('--round-id')
    register.add_argument('--parent-id')
    register.add_argument('--artifact')
    score = commands.add_parser('score', help='attach the host score and optional review to a registered node')
    score.add_argument('--id', required=True)
    score.add_argument('--score', type=float, help='measured score for direct module callers; native clients request host scoring')
    score.add_argument('--review')
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
    if args.command in {'register', 'describe'} and native_client():
        result = describe(args.id, args.title, args.description)
    elif args.command == 'score' and native_client():
        if args.score is not None:
            parser.error('native client scores are registered by the host evaluator; omit --score')
        result = request_score(args.id, args.review)
    elif args.command == 'submit':
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
        elif args.command in {'register', 'describe'}:
            if not args.session_id:
                parser.error('register requires --session-id outside a native improvement client')
            result = search(config).register_description(args.id, args.title, args.description,
                session_id=args.session_id, round_id=args.round_id, parent_id=args.parent_id, artifact=args.artifact)
        elif args.command == 'score':
            if args.score is None:
                parser.error('score requires --score outside a native improvement client')
            result = search(config).register_score(args.id, args.score, args.review)
        else:
            if not config.get('port'):
                parser.error('serve requires a configured port')
            serve(search(config), config['port'])
            return
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
