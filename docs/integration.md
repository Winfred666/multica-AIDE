# Embed the optimizer

Import the module and give each optimization loop its own tree directory. You
can instantiate it twice in one application: an outer solution search and a
per-SOP cost search, for example.

```python
from multica_aide import SearchTree

outer = SearchTree("state/outer-search", drafts=5)
outer.register_baseline("baseline", "Original solution", "Measured baseline", 42,
                        artifact="/snapshots/baseline")
context = outer.next_context("worker-1")
# Restore context["restore_node"]["artifact"], then run your own phases.
# The outer application computes this score after its refinement and review.
outer.register("less-work", "Reduce repeated work", "Reuse verified context", 48,
               session_id="worker-1", round_id=context["round_id"],
               parent_id=context["parent_id"], artifact="/snapshots/less-work",
               review="The official evaluation improved by six points.")

cost = SearchTree("state/sop-search", drafts=2, maximize=False)
cost.register_baseline("baseline", "Original SOP", "Measured execution cost", 1.0)
```

`next_context(session_id)` returns one JSON-compatible object with `mode`
(`draft` or `refine`), `round_id`, `parent_id`, `restore_node`, `tree_summary` and
`knowledge_base`. Repeated calls for that session recover its pending context.
Concurrent clients share one journal and the initial draft budget. A successful
registration completes its session's round; the next call reserves a new one.
If your evaluation fails without a numeric score, handle its evidence and call
`cancel_round(session_id, round_id)` to release that unfinished reservation.

The caller can customize any phase by doing its own work between context and
registration. Keep incomplete proposals and pending evaluations in your own
state. Register only scored solutions; optional node `metadata` can link your
evidence. `snapshot()` returns nodes, the baseline ID and pending sessions for
inspection. Artifacts remain the caller's responsibility.

If later observations change an aggregate metric for an existing node, call
`rescore(node_id, score, review=None)` before selecting the next parent. Your
application retains the original observations and reevaluation evidence. Frozen
benchmark results can remain unchanged throughout their lifetime.
An outer controller can call `next_context(session_id, eligible_ids={...})` to
limit selection to nodes that pass its current phase gates. This preserves prior
scored records. The filter applies when reserving a new round; an existing
pending round remains the session's context until registration or cancellation.

The standalone `install` and `begin` commands are unnecessary for integrations.
For an HTML panel, host just this tree:

```python
from multica_aide.viewer import serve
serve(outer, port=3003)
```

Embed `http://127.0.0.1:3003/` in your panel's iframe, or reuse
`multica_aide.viewer.render_tree(outer.snapshot()["nodes"])` to produce standalone HTML. The
canvas shows the same parent/child tree for an optimization of code or SOPs.
