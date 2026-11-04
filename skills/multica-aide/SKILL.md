---
name: multica-aide
description: Install and run a shared AIDE solution search in one Multica benchmark project, or connect its step-wise tree API to an outer application.
---

For a native issue installation, read [installation](../../docs/installation.md).
Prepare the requested benchmark with [multica-create-bench](../multica-create-bench/SKILL.md)
when its repository or evaluator is missing. Reuse downloaded data, Python
environments and the existing runtime.

Run `python3 run.py --config config.local.yaml install` to preview settings.
Present the resolved draft count, client count, solver and babysitter models, viewer port, knowledge base and
benchmark configuration for requester confirmation before repeating with
`--confirm-config`. Previously confirmed settings remain authorized.
Then run `begin` in the foreground; its Python loop handles normal client rounds.
Each light query babysitter delegates to the shared solver and owns blocked dev
child recovery. The loop wakes it once when a child's blocked state changes.
An exception returns control to you: repair the reported cause and preserve
measured nodes before resuming. Missing submission reminders belong to the loop.

For an outer module, read [integration](../../docs/integration.md). Use
`SearchTree` directly and keep your own evaluation and phase controller.
