"""Prepare one benchmark template; retain each measured solution as a Git worktree."""
import os
from pathlib import Path
import shutil
import subprocess


def git(directory, *args):
    return subprocess.run(['git', '-C', str(directory), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


def checkpoint(directory, message):
    git(directory, 'add', '-A')
    git(directory, '-c', 'user.name=Multica', '-c', 'user.email=multica@localhost',
        'commit', '--quiet', '--allow-empty', '-m', message)
    return git(directory, 'rev-parse', 'HEAD')


def prepare_template(config, skills):
    template = Path(config['template'])
    template.mkdir(parents=True, exist_ok=True)
    if not (template / '.git').exists():
        git(template, 'init', '--quiet')
    shutil.copytree(skills / 'multica-aide-cli', template / '.agents/skills/multica-aide-cli',
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns('.state', '.errors', '__pycache__'))
    with (template / '.gitignore').open('a') as stream:
        stream.write('\n.aide-client.json\n.aide-development.json\n.pi/\n.multica/\n'
                     '**/.state/\n**/.errors/\n__pycache__/\nlogs/\n')
    brief = template / 'AGENTS.d/aide-benchmark.md'
    brief.parent.mkdir(exist_ok=True)
    brief.write_text(config['benchmark']['description'] + '\n')
    return checkpoint(template, 'Prepare benchmark and local CLI procedure')


def add_worktree(config, directory, branch, commit):
    git(config['template'], 'worktree', 'add', '-b', branch, str(directory), commit)
    return str(directory)


def hidden_paths(paths):
    paths = {Path(raw).resolve() for raw in paths}
    return [str(path) for path in sorted(paths) if not any(parent in paths for parent in path.parents)]


def sandbox(policy, directory, argv):
    """Development commands see public files and may write only their solution and /tmp."""
    bwrap = shutil.which('bwrap')
    if not bwrap:
        raise ValueError('Separate validation requires bubblewrap on the development runtime')
    directory = Path(directory).resolve()
    command = [bwrap, '--die-with-parent', '--unshare-user', '--uid', str(os.getuid()),
               '--gid', str(os.getgid()), '--unshare-pid', '--unshare-net', '--cap-drop', 'ALL',
               '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--proc', '/proc', '--dev', '/dev',
               '--bind', str(directory), str(directory)]
    for raw in policy.get('read_only_paths', []):
        if Path(raw).exists():
            command += ['--ro-bind', raw, raw]
    for raw in hidden_paths(policy['deny_paths']):
        path = Path(raw)
        if path.is_dir():
            command += ['--tmpfs', str(path), '--remount-ro', str(path)]
        elif path.exists():
            command += ['--ro-bind', '/dev/null', str(path)]
    return [*command, '--chdir', str(directory), '--', *argv]
