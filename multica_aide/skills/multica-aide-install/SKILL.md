---
name: multica-aide-install
description: Prepare an official benchmark and install a native multica-AIDE campaign.
---

Start with the requested benchmark name, downloaded data and Python environment.
Read the repository's `AGENTS.md` for the benchmark contract. Run
`python3 <this-skill-directory>/wrapper.py` and follow its returned option.
Reuse configuration answers already given. Native Multica supplies the runtime
and sandbox.

Use `--help` to replay the last decision, or read entry guidance for a new run.

In every state, follow returned error guidance and reconcile writes before
retrying. Use `repaired` once for an assertion failure; report a repeated failure
through native Multica block.
