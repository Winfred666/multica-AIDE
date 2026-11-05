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
  guides benchmark preparation, configuration and native client creation. Each
  client follows the [AIDE CLI SOP](multica_aide/skills/multica-aide-cli/SKILL.md)
  through integrity checks, ideas and measured tests. Native Multica provides
  solution development, review and recovery.
- **Embed in a larger application:** use `SearchTree` between the application's
  phases and embed its canvas in an iframe. Independent trees can optimize both
  an outer solution and its SOPs.

Use Python 3.11+ and install with `python3 -m pip install -e .`, or run from a clone
with PyYAML installed. [AGENTS.md](AGENTS.md) gives the two usage paths and benchmark
contract. Direct installation starts with `python3 run.py install`.

[config.yaml](config.yaml) defaults to five drafts, two clients, port 3003,
no knowledge base, solver `gpt-6.1-terra` and babysitter `gpt-6-luna`. Installation
confirms these settings and inherits the installer's native runtime. Local
configuration, datasets, solution artifacts and journals stay outside version control.

The installer runs its controller in the foreground so its native issue task
owns the monitoring lifetime. The controller streams tree changes and client
starts to stderr. The HTML server is independently read-only.

For an embedded tree, a node or structured delivery may include
`display: {"score": "8,200 recorded tokens", "labels": ["12 execution steps"]}`.
These caller-supplied labels control presentation; the numeric score still
controls search and ranking.
