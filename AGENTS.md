# Using multica-AIDE

Use Python 3.11+ and PyYAML. Native tasks supply the `MULTICA_*` credentials.
Use the existing Multica runtime and its default sandbox.
Its native sub-issue SOPs must honor supplied DEV worktrees on arbitrary base
branches and retain independently supplied roots during parent review.

## Install in an issue

Follow [multica-aide-install](multica_aide/skills/multica-aide-install/SKILL.md).
Start its SOP from the checkout:

```sh
python3 run.py install
```

Follow the returned option commands in order. The installing issue prepares the
benchmark and environment, confirms settings, creates the native clients, and
runs the foreground controller and hosts the HTML board. Keep the parent issue active until a
measured solution meets the requested goal. Reuse configuration answers already
given, including draft count D, client count N, agent name, port and knowledge base.
Before editing installed software, stop its controller and native tasks. Preserve
the measured tree and artifacts.

Prepare a dedicated benchmark repository or feature branch at `template` in
`config.local.yaml`. Put its brief in `AGENTS.md` or `AGENTS.d`, and include the
public development data and task files there. Installation commits the prepared
branch with its local CLI SOP once. Each client starts in a DEV worktree checked
out from that commit; the installing parent does not load the client SOP.
The parent's existing project is reused, if any. No shared skills are installed.

Copy `config.yaml` to ignored `config.local.yaml`. The default `mode: val_dev`
uses `AIDE_val_dev` to develop, validate and review in the same issue. This suits
casual tasks and SOP refinement. Explicitly select `mode: separate` when validation
data must remain private. Set `AIDE_solution_validator` and
`AIDE_solution_developer`: each validator owns the CLI and locked validation, and
has one persistent development child, arranged at installation. The child uses
public data and runs development commands through the supplied bubblewrap launcher.
The command sandbox hides `private_paths`, disables network access and allows writes
only to the candidate and temporary files. It does not isolate the agent's
conversation or API access; use a dedicated runtime/workspace if that is required.
Keep private data and the evaluator outside the template and solution repositories.

Agent fields name existing agents exactly; the same agent may fill multiple roles.
Either select an existing agent, or create a new agent with the default model
settings in `config.yaml`. Existing settings stay unchanged. Installation warns
before creating a missing named agent. No issue-specific agents are created.

The benchmark supplies two parts:

- `evaluate_argv`: the official test command, with `{solution}` replaced by the
  candidate repository. It writes a JSON delivery to stdout and fails on malformed
  submissions. The delivery can include metrics, validity and other goal evidence.
- `contract`: a Python file defining `validate(solution) -> bool` for the delivery
  format, `score(delivery) -> number` for ranking, and
  `is_complete(delivery) -> bool` for the requested exit condition. Set
  `maximize: false` for losses. The exit condition must express the requested
  measurable goal; a running process or pending node is not completion.

A valid solution already present in the template enters test directly; otherwise
the first client drafts a minimal baseline. A known measured baseline may include
`baseline_delivery`, `baseline_description` and `baseline_review`. Existing scored
nodes are checked for completion before new work.

Each of the N clients follows
[multica-aide-cli](multica_aide/skills/multica-aide-cli/SKILL.md). `AIDE_cli` runs
that SOP when the package is installed. The SOP checks benchmark integrity, then
supplies the candidate path, selected tree context and official test command.
Each idea needs one description paragraph before test; each measurement needs the
full JSON delivery and one review paragraph. Each solution node keeps its own worktree and Git commit in the tree. Drafts
start from the template; refinements branch directly from the selected node's
commit. Scored worktrees remain available for inspection and reversion. In
separate mode the validator dispatches its existing child and validates its delivery. Official tests use proactive
keyword + time-trigger monitoring; follow recalls in the same turn. Missing
initialization requirements block the client and notify its parent.

The foreground controller starts client 1 to establish a measured root, then enables all
clients. The configured port hosts the read-only tree at `/` and `/tree.json`.
`python3 run.py --config config.local.yaml begin` runs the controller in the
foreground; `serve` hosts the tree independently.

## Embed in a larger module

Use `SearchTree` between the caller's phases and `multica_aide.viewer.render_tree`
for its canvas. Give independent loops separate tree directories. The caller owns
restoration, development, evaluation and termination; embedding runs no installer.
