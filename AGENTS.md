# Using multica-AIDE

Use Python 3.11+ and PyYAML. Run commands from this checkout or use its absolute
`run.py` path. The installed `AIDE_cli` command runs the same protocol. Native
tasks supply the `MULTICA_*` credentials. Each installation owns its solution
tree, artifacts, solver, query clients and their child issues. Use the configured
benchmark project and existing native runtime.

## Native installation

The installing issue owns the requested benchmark and environment preparation.
Subsequent solution work preserves the prepared benchmark contract.
Use the native Multica runtime and its default sandbox.

1. Start from the requested benchmark name. Locate its official task and scoring
   contract, reuse downloaded data and the available Python environment, and
   prepare one benchmark project with a runnable baseline solution repository.
   Bind its repository, public-data and environment resources to this prepared
   benchmark.
   For MLE-bench or AutoExam, use the benchmark's official preparation, split,
   submission format and evaluator. Record the source links in the project.
2. Keep public development inputs available to workers. Put hidden examples,
   answers and the host evaluator outside solution repositories. The evaluator
   receives a solution path through `{solution}`, scores that solution under the
   official contract, and writes `{"score": NUMBER, "review": "optional finding"}`
   to stdout. The host evaluator grades the submitted artifact against private
   grading data. Missing or malformed submissions fail. Use
   `maximize: false` for losses. The installer scores and freezes the baseline.
3. Copy `config.yaml` to ignored `config.local.yaml`. Fill in the project,
   model IDs, baseline repository, public data, private paths and evaluator argv.
   Runtime defaults to the installing agent's runtime. Read its model inventory
   and use its exact IDs for
   `solver_model` and `cli_babysitter_model`; resolve the requested defaults before
   creating clients. Preview the resolved settings:

   ```sh
   python3 run.py --config config.local.yaml install
   ```

   Complete the returned preparation action before running
   `after_preparation_command`. Confirm D, N, solver and babysitter models, viewer
   port, knowledge base and benchmark settings with the requester, reusing answers
   already given. Once installed, run the returned `next_command` to begin.

Repeat confirmed `install` after updating the module or changing models to refresh
the existing agents' protocol and continue with the saved tree.

The installation script guides benchmark and environment preparation, confirms
configuration, and creates N query babysitters and a shared solver with N task
slots. `AIDE_cli` supplies the round protocol: restore the selected repository,
open a query thread, and expose `dev`, `register` and `score` endpoints.
`register --id ID --title TITLE --description DESCRIPTION` records the proposal
with a pending score and its put-forward time. `score --id ID --review TEXT`
requests official host grading and completes the query issue. The host freezes
the finished artifact, registers its score, review and test duration, then opens
the next round. Native Multica owns solution and review workflows, skills and
issue recovery. Missing registrations receive a short reminder; other failures
return to the caller with the saved state.

`begin --max-rounds 1` completes one scored round per client for a bounded live
check. A configured port hosts the HTML tree while `begin` runs; `serve` hosts it
separately. Before editing an installed module, stop the controller and its owned
native tasks. Preserve measured nodes, artifacts and pending round state.

## Embedding

Use the Python API between your own phases. Give independent optimization loops
separate tree directories; the caller owns scoring and artifact restoration.

```python
from multica_aide import SearchTree

outer = SearchTree("state/outer", drafts=5)
outer.register_baseline("baseline", "Original", "Measured baseline", 42,
                        artifact="/snapshots/baseline")
context = outer.next_context("client-1")
outer.register_description("reuse", "Reuse context", "Reduce repeated work",
                           session_id="client-1", round_id=context["round_id"],
                           parent_id=context["parent_id"], artifact="/snapshots/reuse")
# Restore the selected artifact and run your own phases.
outer.register_score("reuse", 48, review="Measured result")

cost = SearchTree("state/sop", drafts=2, maximize=False)
cost.register_baseline("baseline", "Original SOP", "Measured cost", 1.0)
```

`next_context(session_id)` returns JSON-compatible `mode` (`draft` or `refine`),
`round_id`, `parent_id`, `restore_node`, `tree_summary` and `knowledge_base`.
Clients share the initial draft budget and measured tree. Repeated calls recover
that session's pending round. Description registration creates a visible pending
node; finite score registration completes the round and makes it selectable.
Review is optional. AIDE records `put_forward_at`, `scored_at` and
`test_duration_seconds`; the board labels put-forward time and test duration
beneath each score. The caller keeps detailed evaluation evidence.

The same CLI supports direct integration:

```sh
AIDE_cli --config config.local.yaml register --session-id client-1 --id reuse --title 'Reuse context' --description 'Reduce repeated work'
AIDE_cli --config config.local.yaml score --id reuse --score 48 --review 'Measured result'
```

Use `cancel_round(session_id, round_id)` after handling an unscored failure.
`rescore(node_id, score, review=None)` updates a reevaluated aggregate; retain its
observations in the caller. `next_context(session_id, eligible_ids={...})` limits
new parent selections to nodes passing the caller's current gates. This preserves
archived nodes and completed drafts. An existing pending round stays unchanged.
`snapshot()` returns the journal and pending sessions.

Embed the same widget for each tree:

```python
from multica_aide.viewer import render_tree, serve

html = render_tree(outer.snapshot()["nodes"])
# Place the HTML in your panel's iframe, or host it:
serve(outer, port=3003)
```

The read-only server exposes `/` and `/tree.json`. Integrations call the tree API
between their own phases and use the shared canvas in their own panels.
