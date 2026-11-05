"""Review one child with explicit decisions and read-only state checks."""
from sop import Option, Parameter, State, Workflow


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def category(issue):
    return {"done": "done", "closed": "cancelled"}.get(issue.get("status_category"), issue["status"])


def read_issue(context):
    issue = context['cli']('get', context['issue_id'])
    require(issue['id'] == context['issue_id'], 'SOP issue identity changed')
    if issue['workspace_id'] != context['workspace_id']:
        raise context['error']('wrong_workspace', 'Issue is outside the run workspace')
    return issue


def read_child(child_id, context):
    child = context['cli']('get', child_id)
    if child.get('parent_issue_id') != context['issue_id'] or child['workspace_id'] != context['workspace_id']:
        raise context['error']('invalid_child', 'Choose a direct child in this workspace')
    return child


def view(context):
    grouped = context['cli']('children', context['issue_id'])
    children = grouped['unstaged'] + [child for group in grouped['stages'] for child in group['issues']]
    return {'data': {'issue': context['initial'], 'children': children}}


def complete(args, context):
    child = read_child(args.child, context)
    if category(child) != 'in_review':
        raise context['error']('invalid_child', 'Choose a child whose deliverable is ready for review')
    context['cli']('status', child['id'], 'done', '--no-start', write=True)
    return {'data': {'child_id': child['id']}}


def correct(args, context):
    child = read_child(args.child, context)
    if (category(child) in {'done', 'cancelled'} or child.get('assignee_type') != 'agent'
            or not child.get('assignee_id')):
        raise context['error']('invalid_child', 'Choose an open direct child assigned to an agent')
    context['cli']('comment', 'add', child['id'], '--content', args.prompt, write=True)
    return {'data': {'child_id': child['id']}}


def precheck(context):
    issue = read_issue(context)
    if 'result' in context and context['result']['phase'] == 'finished':
        child = read_child(context['args'].child, context)
        require(category(child) == 'done', 'Child completion was not persisted')
    return issue


workflow = Workflow(
    State('review', 'Inspect a child deliverable before accepting it or requesting a correction.',
          initial=True, precheck=precheck, view=view, options=(
              Option('done', 'finished', 'Accept the reviewed child and mark it done.', complete,
                     params=(Parameter('--child', 'Reviewed direct child issue ID'),)),
              Option('resteer', 'resteer', 'Post one concrete correction to an open child.', correct, params=(
                  Parameter('--child', 'Direct child issue ID'), Parameter('--prompt', 'Concrete correction'))),
          )),
    State('resteer', 'Correction sent to the child; stop.', final=True, precheck=precheck),
    State('finished', 'Reviewed child is done; stop.', final=True, precheck=precheck),
)
