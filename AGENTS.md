# Using multica-AIDE

Use Python 3.11+ and PyYAML. Native tasks supply the `MULTICA_*` credentials.
Use the existing Multica runtime and its default sandbox.

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
given, including draft count D, client count N, models, port and knowledge base.
Before editing installed software, stop its controller and native tasks. Preserve
the measured tree and artifacts.

Start with the requested benchmark name, downloaded data and Python environment.
Locate the official task, data split, submission format and evaluator; record the
source links in the benchmark project. For MLE-bench or AutoExam, preserve that
benchmark's official scoring contract. Prepare and bind the benchmark repository,
public data and environment using native Multica resources. Keep private grading
data and the evaluator outside solution repositories.

Copy `config.yaml` to ignored `config.local.yaml` and set the benchmark project,
resource paths and exact available model IDs. Runtime defaults to the installer's
runtime. The default solver is `gpt-6.1-terra`; the client babysitter defaults to
`gpt-6-luna`. Resolve these against the runtime's model inventory before installing.
Store instance benchmark files in the benchmark project or ignored `.aide/`;
keep this module's Git history limited to its portable software.

The benchmark supplies two parts:

- `evaluate_argv`: the official test command, with `{solution}` replaced by the
  candidate repository. It writes a JSON delivery to stdout and fails on malformed
  submissions. The delivery can include metrics, validity and other goal evidence.
- `contract`: a Python file defining `validate(solution) -> bool` for the delivery
  format, `score(delivery) -> number` for ranking, and
  `is_complete(delivery) -> bool` for the requested exit condition. Set
  `maximize: false` for losses. The exit condition must express the requested
  measurable goal; a running process or pending node is not completion.

`benchmark.repository` is optional. With no baseline, the first client receives an
empty candidate and produces a minimal template baseline in the testable delivery
format. A supplied untested baseline enters test directly. A known measured
baseline may include `baseline_delivery`, `baseline_description` and
`baseline_review`. Existing scored nodes are checked for completion before new work.

Each of the N query clients follows
[multica-aide-cli](multica_aide/skills/multica-aide-cli/SKILL.md). `AIDE_cli` runs
that SOP when the package is installed. The SOP checks benchmark integrity, then
supplies the candidate path, selected tree context and official test command.
Each idea needs one description paragraph before test; each measurement needs the
full JSON delivery and one review paragraph. Missing initialization requirements
block the client and notify its parent. Native Multica owns development, review,
sub-issues and exception recovery.

The idea entry creates a dev child for the configured solver. It uses the current
workspace's `multica-subissue-v2/branch_workflow.py` to prepare that candidate's
owned workdir. Follow the returned development issue through native review and
acceptance before testing the candidate. The module supplies no development or
review skills. Score registration requires the dev child's native acceptance.

The foreground controller starts client 1 to establish a measured root, then enables all
clients. The configured port hosts the read-only tree at `/` and `/tree.json`.
`python3 run.py --config config.local.yaml begin` runs the controller in the
foreground; `serve` hosts the tree independently.

## Embed in a larger module

When installing multiple native clients under one parent issue, give each
configuration a distinct `name` and separate tree, state and workdir paths.

Use the Python API between the caller's phases. Give independent optimization
loops separate tree directories. The caller owns restoration, development,
evaluation, phase ordering and termination; it does not run the installer or client
SOP.

```python
from multica_aide import SearchTree

outer = SearchTree("state/outer", drafts=5)
outer.register_baseline("baseline", "Original", "Measured baseline", 42,
                        artifact="/snapshots/baseline")
context = outer.next_context("client-1")
outer.register_description("reuse", "Reuse context", "Reduce repeated work",
                           session_id="client-1", round_id=context["round_id"],
                           artifact="/snapshots/reuse")
# Restore context["restore_node"] and run the caller's phases.
outer.register_score("reuse", 48, review="Measured result")

cost = SearchTree("state/sop", drafts=2, maximize=False)
cost.register_baseline("baseline", "Original SOP", "Measured cost", 1.0)
```

`next_context(session_id)` returns JSON-compatible `mode` (`draft` or `refine`),
`round_id`, `parent_id`, `restore_node`, `tree_summary` and `knowledge_base`.
Concurrent sessions share the draft budget and measured tree. Repeated calls
return the session's pending round. Description registration creates a pending
node; score registration makes it selectable and completes that round. Use
`register_baseline_description(..., session_id=...)` for an untested root.

For structured deliveries, construct the tree with
`score_function=lambda delivery: delivery["metrics"]["loss"]`. Registration keeps
that full JSON as `node["delivery"]` and its finite numeric projection as
`node["score"]`. The caller can evaluate any exit condition on the full delivery.
Review is optional in the embedding API.

Use `cancel_round(session_id, round_id)` after handling an unscored failure.
`rescore(node_id, delivery, review=None)` updates a reevaluated aggregate.
`next_context(session_id, eligible_ids={...})` restricts new parent selection to
nodes passing the caller's gates. `snapshot()` returns nodes and pending sessions.

Embed the same canvas in each optimization panel:

```python
from multica_aide.viewer import render_tree, serve

html = render_tree(outer.snapshot()["nodes"])
# Place this HTML in the caller's iframe, or host it:
serve(outer, port=3003)
```

AIDE records `put_forward_at` at description registration and `scored_at` and
`test_duration_seconds` at score registration. The canvas labels put-forward time
and test duration beneath the score. Keep detailed evaluation evidence with the
caller. Generated state, data, artifacts, logs and local configuration stay ignored.

The canvas accepts optional caller-owned `display` content on a node or its JSON
delivery: `{"score": "8,200 recorded tokens", "labels": ["12 execution steps"]}`.
`score` replaces the visible number; `labels` appear below the timing labels.
The numeric node score continues to rank solutions. AIDE does not interpret the
labels or the caller's evaluation metrics.
