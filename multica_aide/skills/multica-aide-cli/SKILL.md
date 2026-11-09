---
name: multica-aide-cli
description: Follow a sequential AIDE client round, from benchmark integrity and candidate description to measured JSON delivery and exit.
---

Run `python3 .agents/skills/multica-aide-cli/wrapper.py` in this issue's worktree.
Follow its returned recalls. Use the supplied candidate path and benchmark
contract. Describe each idea and review each measured result in one paragraph.
In val_dev mode, improve the supplied solution worktree directly. In separate
mode, use `develop` to dispatch the installed child, then `continue` until its
reviewed delivery is ready for validation. Keep scored worktrees unchanged. Stop when the SOP finishes.
Official tests use proactive keyword + time-trigger monitoring; follow the
returned `continue`/`exception` recall in the same turn. Review the recorded test
JSON; report an unscored failure through native Multica.

Use `--help` to replay the last decision, or read entry guidance for a new run.

In every state, follow returned error guidance and reconcile writes before
retrying. Use `repaired` once for an assertion failure; report a repeated failure
through native Multica block.
