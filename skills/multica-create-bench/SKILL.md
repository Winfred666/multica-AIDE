---
name: multica-create-bench
description: Prepare one Multica benchmark project from its task name and existing data, with a runnable baseline, official host scoring and hidden-data isolation for AIDE clients.
---

Create a usable benchmark from the requested name. Reuse the supplied downloaded
data, Python environment and existing Multica runtime. Locate the benchmark's
official task definition and scoring contract; record their source links in the
project. For MLE-bench tasks use the published MLE-bench preparation and grader,
including its train/validation split and submission schema. For AutoExam use its
official dataset and evaluator. Preserve the benchmark's score direction and
accepted submission format.

Prepare one project with its task instructions, public development data, a
baseline solution repository, and a host evaluator. The repository must produce
a valid submission from public inputs. Put hidden examples, answers and the host
evaluator outside solution checkouts. Keep credentials and hidden contents out
of issue descriptions. Agents may read public schema and run public checks.

The host evaluator accepts a solution repository path, runs or reads its
submission under the benchmark's official contract and prints
`{"score": NUMBER, "review": "optional measured finding"}`. It must score the
solution supplied on that invocation. A missing or malformed submission fails;
it cannot reuse an earlier output. Run it on the initial solution and preserve
the measured result as the baseline.

Configure private paths and an existing runtime that sandboxes
every agent tool in `config.local.yaml`. Default native runtimes do not enforce
issue policy metadata, and SOP command wrappers cover only their own commands.
Supply a probe that executes through that runtime with the configured profile and policy,
attempts private reads, reads a public input and writes a solution file. Return
the observed `runtime_id`, `private_data_visible: false`, `public_data_readable:
true` and `solution_writable: true`. Follow the probe arguments in
[installation](../../docs/installation.md). The existing launcher must enforce the
policy on query clients and their development children. Report a missing launcher
as a setup blocker; do not replace it with an expected-result printout.

Return the project ID, baseline checkout, public data path, private paths,
evaluator argv, score direction, runtime/model ID and successful runtime probe.
The installing agent completes the configuration preview and confirmation before
starting clients. If preparation fails, fix the concrete setup cause and keep
this guidance consistent with the supported workflow.
