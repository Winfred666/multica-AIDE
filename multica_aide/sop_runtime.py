#!/usr/bin/env python3
"""Reusable SOP runtime. Customize controller.py; run this file for JSON guidance."""

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import traceback
import uuid
from types import SimpleNamespace
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
ERROR_REPO = ""
RULES = {
    "identity": "MULTICA_TASK_ID is required; state is keyed by that exact value.",
    "state": "Each task owns .state/<task_id>.json; read it directly and replace atomically.",
    "transitions": "Controller phases advance on callbacks; wrapper assertions can enter error or blocked; completion deletes the task record for reuse in the same native task.",
    "callbacks": "Choose a named option valid in the recorded state; init starts or resumes without an action.",
    "failure": "Assertion failures enter error; one repaired retry may recover or enter blocked. Reconcile external writes first.",
    "repair": "Add a reproducing regression fixture, repair minimally, retry once; stop if still failing.",
}
PATTERNS = (
    (r"(?i)permission denied|read-only file system", "Check access to the named path; preserve its contents."),
    (r"(?i)no such file|not found|command not found", "Correct the missing path or executable, then retry once."),
    (r"(?i)conflict|unmerged", "Resolve the reported conflict and rerun the same action once."),
    (r"(?i)timed? out|connection|rate limit", "Check connectivity or the stated rate-limit reset, then retry once."),
)


class SOPError(Exception):
    def __init__(self, code, message, hint=None):
        super().__init__(message)
        self.code, self.hint = code, hint


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SOPError("invalid_input", message, "Use --help for JSON command guidance; correct the input.")


def arguments(argv, controller):
    parser = Parser(add_help=False, allow_abbrev=False)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("event", nargs="?", default="init")
    action.add_argument("--error")
    action.add_argument("--error-file", type=Path)
    action.add_argument("--help", action="store_true")
    controller.workflow.configure(parser)
    args, args_extra = parser.parse_known_args(argv)
    args.extra = args_extra
    return args


def callback(instruction, args=None):
    if args is None:
        return {"instruction": instruction}
    argv = [sys.executable, str(ROOT / "wrapper.py"), *args]
    return {"instruction": instruction, "command": shlex.join(argv)}


def validate_package():
    required = ('SKILL.md', 'config.json', 'wrapper.py', 'controller.py', 'sop.py', 'monitor.py')
    for name in required:
        path = ROOT / name
        if not path.is_file() or not path.read_text().strip():
            raise ValueError('Incomplete symbolic SOP: ' + name)
        if name.endswith('.py'):
            compile(path.read_text(), str(path), 'exec')
    config = json.loads((ROOT / 'config.json').read_text())
    if not isinstance(config, dict) or not isinstance(config.get('DEBUG'), bool):
        raise ValueError('config.json needs a boolean DEBUG')
    skill = (ROOT / 'SKILL.md').read_text()
    header = skill.split('---', 2)
    if len(header) != 3 or header[0].strip():
        raise ValueError('SKILL.md needs YAML frontmatter')
    name = re.search(r'^name:\s*([A-Za-z0-9][A-Za-z0-9-]*)\s*$', header[1], re.M)
    if not name or not re.search(r'^description:\s*\S', header[1], re.M):
        raise ValueError('SKILL.md needs a name and description')
    return name[1]


def load_controller():
    name = validate_package()
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("sop_controller", ROOT / "controller.py")
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    controller.SOP_NAME = name
    config = ROOT / 'config.json'
    controller.DEBUG = config.exists() and json.loads(config.read_text()).get('DEBUG') is True
    from sop import Workflow
    if not isinstance(getattr(controller, 'workflow', None), Workflow):
        raise ValueError('Controller must export a Workflow object named workflow')
    return controller


def multica(*args):
    command = ["multica", "issue", *args, "--output", "json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise SOPError("cli_error", f"{shlex.join(command)}: {result.stderr.strip() or result.stdout.strip()}")
    return json.loads(result.stdout)


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def state_path(context):
    return ROOT / '.state' / (context['task_id'] + '.json')


def read_state(context, phases):
    path = state_path(context)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise SOPError('invalid_state', 'Invalid task state JSON: ' + str(error)) from error
    if not isinstance(state, dict) or state.get('status') not in phases:
        raise SOPError('invalid_state', 'Task state must contain a declared status')
    return state


def save_state(context, state):
    path = state_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(state, default=str, allow_nan=False))


def help_output(context):
    path = state_path(context)
    if path.exists():
        state = json.loads(path.read_text(encoding='utf-8'))
        return json.dumps(redact(state.get('output', state)), allow_nan=False)
    return (ROOT / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)[2].strip()


def assertion_before(workflow, context):
    context['assertion'] = {'boundary': 'before'}
    initial = workflow.before(context)
    context.pop('assertion')
    return initial


def assertion_after(workflow, context, args, initial, result):
    context['assertion'] = {'boundary': 'after', 'initial': initial,
                            'args': vars(args), 'result': result,
                            'path_args': {name: isinstance(value, list) for name, value in vars(args).items()
                                          if isinstance(value, Path) or isinstance(value, list) and value and isinstance(value[0], Path)}}
    workflow.after(context, args, initial, result)
    context.pop('assertion')


def recovery_result(receipt, status, context):
    blocked = status == 'blocked'
    detail = receipt['error']
    if blocked:
        instruction = 'Assertion still fails. Return the evidence to the calling SOP for a fixture and correction.'
        own_name = validate_package()
        target = ROOT.parent / 'multica-surface-block' / 'wrapper.py'
        next_step = callback(instruction)
        if own_name != target.parent.name and target.is_file():
            argv = [sys.executable, str(target)]
            next_step['command'] = shlex.join(argv)
        else:
            next_step['instruction'] += ' Use the calling SOP or parent recovery path; do not recurse into this failed SOP.'
    else:
        repair_args = ['repaired']
        next_step = callback('Correct the unmet assertion, reconcile writes, preserve a fixture, then run repaired once.',
                             repair_args)
    return {'ok': False, 'accepted': False, 'finished': False, 'blocked': blocked,
            'status': status, 'state_changed': False, 'error': detail, 'next_step': next_step,
            'evidence': str(state_path(context)),
            'external_write_attempted': receipt.get('external_write_attempted', False),
            'data': receipt.get('assertion', {}).get('result', {}).get('data')}


def validate_result(result, phase, workflow, args):
    next_phase = result['phase']
    accepted = result.get('accepted', True)
    if next_phase not in workflow.states:
        raise ValueError('Controller returned an undeclared phase')
    if not isinstance(accepted, bool):
        raise ValueError('Controller accepted must be a boolean')
    if not accepted and next_phase != phase:
        raise ValueError('Rejected choices must preserve the phase')
    if args.event == 'init' and next_phase != phase and not workflow.states[next_phase].final:
        raise ValueError('Only an accepted agent callback may advance the phase')


def transition(args, context, controller):
    workflow = controller.workflow
    phases = set(workflow.states)
    state = read_state(context, phases | {'error', 'blocked'})
    context['state'] = state
    before = state.get('status')
    context['prior_status'] = before
    phase = before or workflow.initial
    receipt = None
    if phase in {'error', 'blocked'}:
        receipt = state['recovery']
        if any(receipt[key] != context[key] for key in ('task_id', 'workspace_id', 'issue_id')):
            raise SOPError('wrong_recovery', 'Assertion receipt belongs to another issue or workspace')
        if phase == 'blocked' or args.event == 'init':
            return recovery_result(receipt, before, context)
        if args.event != 'repaired':
            raise SOPError('invalid_transition', 'Use the returned repaired callback after correcting the assertion')
        context['argv'] = receipt['argv']
        phase = receipt['phase']
        args = arguments(receipt['argv'], controller)
    if args.error is not None or args.error_file is not None:
        message = args.error if args.error is not None else args.error_file.read_text(encoding='utf-8')
        context['reported_error'] = message
        hint = next((hint for pattern, hint in PATTERNS if re.search(pattern, message)), None)
        raise SOPError('action_error', message or 'Agent reported an empty error', hint)
    if args.event == 'quit' and phase == workflow.initial and not args.extra:
        context['quitted'] = True
        context['state'] = state
        message = f'SoP {controller.SOP_NAME} quitted'
        raise SOPError('quitted', message, message)

    def cli(*command, write=False):
        record = {'argv': list(command), 'write': write, 'succeeded': False}
        context.setdefault('commands', []).append(record)
        output = multica(*command)
        record['succeeded'] = True
        return output

    if not context['issue_id']:
        raise SOPError('missing_issue_id', 'MULTICA_ISSUE_ID is required')
    context.update(cli=cli, error=SOPError)
    invalid = None
    try:
        if receipt and receipt['assertion']['boundary'] == 'after':
            saved = receipt['assertion']
            result = saved['result']
            values = dict(saved['args'])
            for name, multiple in saved['path_args'].items():
                values[name] = [Path(value) for value in values[name]] if multiple else Path(values[name])
            assertion_after(workflow, context, SimpleNamespace(**values), saved['initial'], result)
        else:
            context['phase'] = phase
            context['args'] = args
            try:
                initial = assertion_before(workflow, context)
            except Exception as error:
                from sop import Completed
                if not isinstance(error, Completed):
                    raise
                context.pop('assertion', None)
                initial = context.get('initial')
                result = error.result
            else:
                try:
                    if before is None and args.event != 'init':
                        raise SOPError('invalid_transition', 'Start this SOP with a bare call or init before choosing an option')
                    result = workflow.step(args, context, phase)
                except SOPError as error:
                    if error.code not in {'invalid_input', 'invalid_transition'}:
                        raise
                    invalid = {'code': error.code, 'message': str(error)}
                    result = workflow.guidance(workflow.states[phase], context)
                    result['accepted'] = False
            validate_result(result, phase, workflow, args)
            if invalid is None and (args.event != 'init' or result['phase'] != phase):
                assertion_after(workflow, context, args, initial, result)
    except AssertionError as error:
        if 'assertion' not in context:
            raise
        clean_context = json.loads(json.dumps({k: v for k, v in context.items() if k not in {'cli', 'error', 'args'}}, default=str))
        failure = exception_result(error, clean_context)
        status = 'blocked' if receipt else 'error'
        saved = {**clean_context, 'phase': phase, 'args': vars(args),
                 'error': failure['error'], 'external_write_attempted': failure['external_write_attempted']}
        state.update(status=status, recovery=saved)
        save_state(context, state)
        result = recovery_result(saved, status, context)
        result['state_changed'] = True
        return result
    finally:
        context.pop('cli', None)
        context.pop('error', None)
    if result['phase'] == workflow.initial:
        result.setdefault('alternatives', []).append({'instruction': 'Stop this SOP.', 'callback': ['quit']})
    next_phase = result.pop('phase')
    result.setdefault('accepted', True)
    status = next_phase if result['accepted'] or receipt else before
    result['next_step'] = callback(result.pop('instruction'), result.pop('callback', None))
    result['next_step']['option_instruction'] = result.pop('callback_instruction', None)
    alternatives = result.pop('alternatives', [])
    if alternatives:
        result['next_step']['alternatives'] = [callback(choice['instruction'], choice['callback'])
                                                for choice in alternatives]
    finished = workflow.states[next_phase].final
    changed = before != (None if finished else status)
    result.update(ok=invalid is None, status=status, finished=finished,
                  state_changed=changed, error=invalid)
    json.dumps(result, allow_nan=False)
    if finished:
        state_path(context).unlink(missing_ok=True)
    elif changed:
        state.pop('recovery', None)
        state['status'] = status
        save_state(context, state)
    return result


def redact(value):
    secrets = [v for k, v in os.environ.items() if len(v) >= 4 and re.search(r"(?i)token|password|secret|api.?key|credential", k)]

    def clean(item):
        if isinstance(item, dict):
            return {key: clean(part) for key, part in item.items()}
        if isinstance(item, list):
            return [clean(part) for part in item]
        if not isinstance(item, str):
            return item
        for secret in sorted(secrets, key=len, reverse=True):
            item = item.replace(secret, "[REDACTED]")
        item = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|(?:mat|sk)_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]+)", "[REDACTED]", item)
        item = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+", r"\1[REDACTED]", item)
        return re.sub(r'''(?i)((?:password|token|secret|api[_-]?key)["']?\s*[:=]\s*)(?:\[REDACTED\]|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)''', r"\1[REDACTED]", item)

    return clean(value)


def github(*argv, payload=None):
    result = subprocess.run(["gh", "api", "--hostname", "github.com", *argv],
                            input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=10,
                            env={**os.environ, "GH_PROMPT_DISABLED": "1"})
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"gh exited {result.returncode}")
    return json.loads(result.stdout)


def deliver_pending():
    destination = os.environ.get("SYMBOLIC_SOP_ERROR_REPO", "").strip()
    if not destination:
        return {"status": "local", "reason": "external reporting is not configured"}
    outbox = ROOT / ".errors"
    if not outbox.exists():
        return {"status": "none"}
    try:
        pending = sorted(outbox.glob("*.json"))
        if not pending:
            return {"status": "none"}
        path = pending[0]
        incident = json.loads(path.read_text(encoding="utf-8"))
        repo = destination
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Configure SYMBOLIC_SOP_ERROR_REPO as owner/repo")
        metadata = github("repos/" + repo)
        if metadata.get("private") is not True or metadata.get("full_name", "").lower() != repo.lower():
            raise ValueError("Error destination must be the configured private repository")
        details = json.dumps(redact(incident), indent=2, ensure_ascii=True)
        chunks = [details[start:start + 18000] for start in range(0, len(details), 18000)]
        issue = github("--method", "POST", f"repos/{repo}/issues", "--input", "-", payload={
            "title": f"[symbolic-sop] {incident['code']} ({incident['incident_id']})",
            "body": "Error report (JSON; continuation in comments if needed):\n\n" + chunks[0],
        })
        for chunk in chunks[1:]:
            github("--method", "POST", f"repos/{repo}/issues/{issue['number']}/comments", "--input", "-",
                   payload={"body": "Error report continued:\n\n" + chunk})
        path.unlink()
        return {"status": "sent", "incident_id": incident["incident_id"], "issue_url": issue["html_url"], "remaining": len(pending) - 1}
    except Exception as error:
        return redact({"status": "queued", "error": f"{type(error).__name__}: {error}", "outbox": str(outbox)})


def exception_result(error, context):
    assertion_failure = isinstance(error, AssertionError) and bool(context.get('assertion'))
    hint = error.hint if isinstance(error, SOPError) else None
    if assertion_failure:
        hint = 'Correct the unmet live-state assertion without weakening it; reconcile writes and use the wrapper repair callback once.'
    hint = hint or next((hint for pattern, hint in PATTERNS if re.search(pattern, str(error))), None)
    code = "assertion_failed" if assertion_failure else error.code if isinstance(error, SOPError) else "controller_failure"
    detail = {"code": code, "type": type(error).__name__, "message": str(error),
              "hint": hint or "Create a reproducing regression fixture; repair the controller under its rules, then retry this call once. Stop if the repair still fails."}
    if hint is None or not isinstance(error, SOPError):
        detail["rules"] = RULES
        detail["workflow_rules"] = context.get("controller_rules", "Read controller.py; preserve the workflow contract.")
        detail["fixture_request"] = "Preserve the failing argv, relevant Multica IDs, and prior task JSON record; add a failing local fixture before changing the script."
    detail["incident_id"] = uuid.uuid4().hex
    incident = {**context, "incident_id": detail["incident_id"], "timestamp": datetime.now(timezone.utc).isoformat(),
                "repository": os.environ.get("SYMBOLIC_SOP_ERROR_REPO", ERROR_REPO),
                "code": code, "error": detail, "traceback": traceback.format_exc()}
    try:
        incident["script_sha256"] = hashlib.sha256((ROOT / "controller.py").read_bytes()).hexdigest()
        incident["wrapper_sha256"] = hashlib.sha256((ROOT / "wrapper.py").read_bytes()).hexdigest()
        incident["cwd"] = str(Path.cwd())
    except OSError:
        pass
    incident = redact(incident)
    writes = [command for command in context.get("commands", []) if command["write"]]
    write_attempted = bool(writes) or context.get('external_write_attempted', False)
    retry = context["argv"] if not isinstance(error, SOPError) and not write_attempted else []
    recovery = callback(detail["hint"], retry)
    if write_attempted:
        recovery["instruction"] += " A Multica write was attempted: inspect its live result before repeating it; resume to reconcile."
    result = {"ok": False, "status": context.get("prior_status"), "accepted": False, "finished": False,
              "state_changed": False, "error": redact(detail),
              "next_step": recovery, "external_write_attempted": write_attempted}
    if "native_result" in context:
        result.update(data={key: value for key, value in context['native_result'].items()
                            if key not in {'ok', 'error', 'instruction'}},
                      returncode=context["native_returncode"])
    try:
        outbox = ROOT / ".errors"
        outbox.mkdir(mode=0o700, exist_ok=True)
        path = outbox / (incident["timestamp"].replace(":", "-") + "-" + incident["incident_id"] + ".json")
        atomic_write(path, json.dumps(incident, indent=2))
        result["reporting"] = deliver_pending()
        result["reporting"]["current_incident_queued"] = path.exists()
    except Exception as reporting_error:
        result["reporting"] = redact({"status": "failed", "error": str(reporting_error), "incident": incident})
    return result


def response(result):
    """Agent guidance; diagnostics remain in the saved incident."""
    step = result['next_step']
    instruction = step['instruction']
    if result['status'] is not None:
        instruction = f'You are in SOP state "{result["status"]}". {instruction}'
    options = []
    if step.get('command'):
        options.append({'instruction': step.get('option_instruction') or step['instruction'],
                        'command': step['command']})
    options.extend(step.get('alternatives', []))
    output = {'status': result['status'], 'instruction': instruction, 'next_step': options}
    if result.get('error'):
        output['error'] = {key: result['error'][key] for key in ('code', 'message')}
    for key in ('data', 'evidence', 'usage'):
        value = result.get(key)
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if v not in (None, '', [], {})}
        if value not in (None, '', [], {}):
            output[key] = value
    return output


def upload_artifact(filename, content):
    """Replace this agent's named live deliverable on the native task issue."""
    if not all(os.environ.get(key) for key in
               ('MULTICA_SERVER_URL', 'MULTICA_TOKEN', 'MULTICA_TASK_ID', 'MULTICA_ISSUE_ID', 'MULTICA_WORKSPACE_ID')):
        return
    boundary = 'sop-' + uuid.uuid4().hex
    fields = {'task_id': os.environ['MULTICA_TASK_ID'], 'issue_id': os.environ['MULTICA_ISSUE_ID']}
    body = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
                    for key, value in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
             'Content-Type: text/html\r\n\r\n').encode() + content.encode() + f'\r\n--{boundary}--\r\n'.encode()
    request = Request(os.environ['MULTICA_SERVER_URL'].rstrip('/') + '/api/upload-file', data=body, headers={
        'Authorization': 'Bearer ' + os.environ['MULTICA_TOKEN'],
        'X-Workspace-ID': os.environ['MULTICA_WORKSPACE_ID'],
        'Content-Type': 'multipart/form-data; boundary=' + boundary})
    # A preview failure must never repeat a completed workflow action.
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except Exception as error:
        print(redact('Live artifact update failed: ' + str(error)), file=sys.stderr)


def state_artifact(context, controller, result):
    """Render the controller's workflow and highlight the current or failed state."""
    status = result['status']
    recovery = context.get('state', {}).get('recovery', {})
    assertion = recovery.get('assertion', {})
    phase = status
    if result.get('error'):
        phase = (assertion.get('result', {}).get('phase') or recovery.get('phase') or
                 context.get('phase') or context.get('prior_status') or controller.workflow.initial)
    states = controller.workflow.states
    diagram = 'flowchart LR\n' + '\n'.join(
        f'{name}["{name}"]' for name in states)
    for branch in controller.workflow.branches.values():
        diagram += f'\n{branch.name}{{"{branch.description}"}}'
        diagram += '\n' + '\n'.join(f'{branch.name} -->|{label}| {target}'
                                     for label, target in branch.routes.items())
    edges = {(state.name, option.name): option.target for state in states.values()
             if not callable(state.options) for option in state.options}
    for source, targets in context.get('state', {}).get('option_targets', {}).items():
        edges.update({(source, name): target for name, target in targets.items()})
    logic = dict(context.get('state', {}).get('option_diagrams', {}))
    for state in states.values():
        if not callable(state.options):
            logic.setdefault(state.name, {option.name: option.diagram() for option in state.options})
    diagram += '\n' + '\n'.join(f'{source} __option_{index}@-->|{option}| {target}'
                                for index, ((source, option), target) in enumerate(edges.items()))
    # Boundary markers are diagram-only; they never introduce workflow options.
    boundaries = [f'__start@{{ shape: sm-circ }} --> {controller.workflow.initial}']
    boundaries.extend(f'{state.name} --> __end@{{ shape: fr-circ }}' for state in states.values() if state.final)
    diagram += '\n' + '\n'.join(boundaries)
    if phase in states:
        color = '#fee2e2' if result.get('error') else '#dcfce7'
        diagram += f'\nclassDef current fill:{color},stroke:#2563eb,stroke-width:3px\nclass {phase} current\n'
    details = {'issue_id': os.environ['MULTICA_ISSUE_ID'], 'task_id': context['task_id'], 'status': status}
    if result.get('error'):
        details.update(failed_state=phase, boundary=assertion.get('boundary'), error=result['error'])
    nodes = {name: decision for name, decision in context.get('state', {}).get('decisions', {}).items()
             if name in states}
    for state in states.values():
        if state.name in nodes:
            continue
        # Unvisited states have declared guidance only; never run their views or option factories for a preview.
        choices = [callback(option.prompt, option.callback()) for option in state.options] if not callable(state.options) else []
        if state.initial:
            choices.append(callback('Stop this SOP.', ['quit']))
        nodes[state.name] = response({'status': state.name,
                                     'next_step': {**callback(state.description), 'alternatives': choices}})
    if context.get('output'):
        nodes[phase] = context['output']
    links = {f'__option_{index}': {
        'decision': next((option for option in nodes[source]['next_step']
                          if shlex.split(option['command'])[2] == name), None),
        'logic': {name: logic.get(source, {}).get(name, '')}}
        for index, (source, name) in enumerate(edges)}
    payload = json.dumps(redact({'nodes': nodes, 'edges': links, 'decision': nodes[phase], 'logic': logic})).replace('<', '\\u003c')
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>' + escape(controller.SOP_NAME) + '</title>'
            '<style>body{font:15px system-ui;margin:8px;color:#172033}pre{white-space:pre;overflow-x:auto}'
            '.mermaid{margin:0;max-width:100%;overflow-x:auto}.mermaid svg{display:block;max-width:none;margin:0}'
            '@media(orientation:landscape){.mermaid{overflow-x:hidden}.mermaid svg{max-width:100%;height:auto}}'
            '[role="button"]{cursor:pointer}.flowchart-link{pointer-events:stroke}'
            'h1{font-size:18px}</style>'
            '<pre class="mermaid" id="workflow">' + escape(diagram) + '</pre>'
            '<h1>' + escape(controller.SOP_NAME) + '</h1><pre id="detail" aria-live="polite">'
            + escape(json.dumps(redact(nodes[phase]), indent=2)) + '</pre>'
            '<div id="logic"></div>'
            '<pre id="metadata">' + escape(json.dumps(redact(details), indent=2)) + '</pre>'
            '<script id="sop-data" type="application/json">' + payload + '</script>'
            '<script src="https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js"></script>'
            '<script type="module">const mermaid=window.mermaid;'
            'const data=JSON.parse(document.getElementById("sop-data").textContent),detail=document.getElementById("detail"),logic=document.getElementById("logic");'
            'const show=(value,charts={})=>{detail.textContent=JSON.stringify(value,null,2);logic.replaceChildren();'
            'for(const [name,source] of Object.entries(charts)){if(!source)continue;'
            'const heading=document.createElement("h2"),chart=document.createElement("pre");'
            'heading.textContent=name;chart.className="mermaid";chart.textContent=source;logic.append(heading,chart)}'
            'mermaid.run({nodes:logic.querySelectorAll(".mermaid")})};'
            'mermaid.initialize({startOnLoad:false,securityLevel:"strict",flowchart:{useMaxWidth:false}});await mermaid.run({querySelector:".mermaid"});'
            'const bind=(element,value,charts={})=>{if(!element||!value)return;element.setAttribute("tabindex","0");element.setAttribute("role","button");'
            'element.onclick=()=>show(value,charts);element.onkeydown=event=>{if(event.key==="Enter"||event.key===" "){'
            'event.preventDefault();show(value,charts)}}};'
            'for(const [name,value] of Object.entries(data.nodes)){'
            'const node=document.querySelector(`#workflow .node[id*="flowchart-${name}-"]`);bind(node,value,data.logic[name])}'
            'for(const [id,edge] of Object.entries(data.edges)){'
            'bind(document.querySelector(`#workflow .edgePaths [data-id="${id}"]`),edge.decision,edge.logic);'
            'bind(document.querySelector(`#workflow .edgeLabel [data-id="${id}"]`)?.parentElement,edge.decision,edge.logic)}'
            'show(data.decision,data.logic[data.decision.status]);'
            '</script></html>')


def publish_artifacts(context, controller, result):
    if not controller or not all(os.environ.get(key) for key in
            ('MULTICA_SERVER_URL', 'MULTICA_TOKEN', 'MULTICA_TASK_ID', 'MULTICA_ISSUE_ID', 'MULTICA_WORKSPACE_ID')):
        return
    shifted = result.get('state_changed') or result.get('finished')
    if not (shifted or result.get('error') or
            result.get('accepted') and context['argv'] and context['argv'][0] != 'init'):
        return
    try:
        # In DEBUG mode, update the controller workflow's state chart artifact on every state shift.
        if controller.DEBUG and shifted:
            upload_artifact(controller.SOP_NAME + '-state.html', state_artifact(context, controller, result))
        if not result.get('error') and hasattr(controller, 'artifacts'):
            for filename, content in controller.artifacts({**context, 'cli': multica, 'error': SOPError}).items():
                upload_artifact(filename, content)
    except Exception as error:
        print(redact('Live artifact update failed: ' + str(error)), file=sys.stderr)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ['--validate']:
        try:
            validate_package()
            load_controller()
            print(json.dumps({'status': 'valid'}))
            return 0
        except Exception as error:
            print(json.dumps({'status': 'error', 'error': str(error)}))
            return 1
    task_id = os.environ.get("MULTICA_TASK_ID")
    context = {"task_id": task_id, "agent_id": os.environ.get("MULTICA_AGENT_ID"),
               "issue_id": os.environ.get("MULTICA_ISSUE_ID"),
               "workspace_id": os.environ.get("MULTICA_WORKSPACE_ID"),
               "argv": argv, "script": str(ROOT / "controller.py"),
               "wrapper": str(ROOT / "wrapper.py")}
    controller = None
    try:
        if not task_id or any(c.isspace() or c in '/\\' for c in task_id):
            raise SOPError("missing_task_id", "MULTICA_TASK_ID must be present and contain no whitespace or path separators", "Restore MULTICA_TASK_ID from the Multica run environment; do not invent an ID.")
        if argv == ['--help']:
            print(help_output(context))
            return 0
        controller = load_controller()
        context['skill'] = getattr(controller, 'SOP_NAME', None)
        args = arguments(context['argv'], controller)
        if getattr(args, 'workspace', None) and args.workspace != context['workspace_id']:
            raise SOPError('wrong_workspace', '--workspace must match the native run workspace')
        for flag, key in (('issue', 'issue_id'), ('workspace', 'workspace_id')):
            if getattr(args, flag, None):
                context[key] = getattr(args, flag)
        context['controller_rules'] = 'Follow controller.workflow options and state prechecks.'
        if args.help:
            print(help_output(context))
            return 0
        result = transition(args, context, controller)
        result["reporting"] = deliver_pending()
    except Exception as error:
        result = exception_result(error, context)
        if context.get('quitted'):
            state_path(context).unlink(missing_ok=True)
            result.update(status='quitted', state_changed=True, next_step=callback(str(error)))
    output = redact(response(result))
    if controller and context.get('state'):
        state = context['state']
        state['output'] = output
        state.setdefault('decisions', {})[output['status']] = output
        state.setdefault('option_diagrams', {}).update(context.get('option_diagrams', {}))
        state.setdefault('option_targets', {}).update(context.get('option_targets', {}))
        if state_path(context).exists():
            save_state(context, state)
    context['output'] = output
    publish_artifacts(context, controller, result)
    print(json.dumps(output, allow_nan=False))
    return 0 if result["ok"] else result.get("returncode", 1)


if __name__ == "__main__":
    sys.exit(main())
