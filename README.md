# multica-AIDE

A shared solution tree for Multica optimization, following
[Weco's AIDE](https://github.com/wecoai/aideml). Clients draft independent solutions,
then refine the best measured node. Each node holds an ID, title, description,
score, optional review and artifact reference.

Register the description first with a pending score, then the measured delivery
and review. Deliveries can be JSON; the benchmark supplies a numeric ranking
function and an exit condition on the full delivery. The HTML canvas shows the
tree, put-forward time and test duration.

Use it in two ways:

- **Install in an issue:** the [installation SOP](multica_aide/skills/multica-aide-install/SKILL.md)
  guides benchmark preparation, configuration and client issue creation. Each
  client follows the [AIDE CLI SOP](multica_aide/skills/multica-aide-cli/SKILL.md)
  through integrity checks, ideas and measured tests.
- **Embed in a larger application:** use `SearchTree` between the application's
  phases and embed its canvas in an iframe. Independent trees can optimize both
  an outer solution and its SOPs.

Use Python 3.11+ and install with `python3 -m pip install -e .`, or run from a clone
with PyYAML installed. [AGENTS.md](AGENTS.md) gives the two usage paths and benchmark
contract. Direct installation starts with `python3 run.py install`.

[config.yaml](config.yaml) selects one of two modes:

- `val_dev` (default): `AIDE_val_dev` owns development, validation and review.
- `separate`: `AIDE_solution_validator` owns the CLI and locked validation;
  `AIDE_solution_developer` works in its persistent child through bubblewrap.
  Select this explicitly when private validation matters. The sandbox covers
  development commands, not the agent conversation or API access.

Agent values are exact names. Either select an existing agent, or create a new
agent with the default settings in the configuration. Existing agents are reused
unchanged; missing-agent creation emits a warning. Roles may use the same agent.

Prepare a dedicated `template` repository or feature branch with the benchmark,
public data and `AGENTS.md`/`AGENTS.d`. Installation commits its local CLI SOP once;
clients check out that template as their main worktrees beneath `workdirs`.
Every solution node records its own worktree and commit. Refinement branches from
the selected commit, without copying or restoring solution directories. The parent
uses the installer only. No shared skills or benchmark projects are installed.
Generated worktrees, journals, datasets and local settings remain unversioned in
this software repository.

The installer runs its controller in the foreground so its native issue task
owns the monitoring lifetime. The controller streams tree changes and client
starts to stderr. The HTML server is independently read-only.

Official tests log between foreground monitor recalls. Keyword and time triggers
return new output with `continue` and `exception` choices; exit advances to review.

For an embedded tree, a node or structured delivery may include
`display: {"score": "8,200 recorded tokens", "labels": ["12 execution steps"]}`.
These caller-supplied labels control presentation; the numeric score still
controls search and ranking.
