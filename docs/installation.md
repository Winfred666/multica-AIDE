# Install in a Multica issue

Give a setup agent the benchmark name, this checkout and permission to run the
optimization loop. The agent uses
[multica-create-bench](../skills/multica-create-bench/SKILL.md) to prepare one
benchmark project and a scored baseline repository with the existing runtime.
It then follows [multica-aide](../skills/multica-aide/SKILL.md).

Copy `config.yaml` to an ignored `config.local.yaml`. Set the benchmark project,
existing runtime, model, baseline repository, public data and host evaluator.
`solver_model` defaults to `gpt-6.1-terra`; `cli_babysitter_model` defaults to
`gpt-6-luna`. Use provider-qualified IDs when the runtime requires them.
The evaluator receives `{solution}` as a command argument and writes one JSON
object to stdout:

```json
{"score": 0.78, "review": "Optional measured finding"}
```

The score must be numeric and finite. Set `maximize: false` for a loss. The
controller evaluates the baseline and each submitted checkout outside the agent
sandbox. Workers cannot register their own claimed score through this mode.
List the hidden evaluator and private dataset in `benchmark.private_paths`.

Select an existing runtime that enforces the filesystem policy
on every agent tool, including query clients and their dev children. Set its
sandbox profile as `sandbox.profile`. Default native runtimes do not enforce
issue `sandbox_policy` metadata; a wrapper around individual SOP commands also
leaves other tools outside the boundary. If the full-runtime launcher is missing,
repair that existing installation before running AIDE.

Supply `probe_argv` for that runtime. It receives `--profile NAME --runtime-id ID
--policy JSON`, launches through the selected runtime, attempts
private reads, reads a public input and writes a solution file. After those actual
operations succeed or fail as required, it returns:

```json
{"runtime_id": "CONFIGURED_RUNTIME_ID", "private_data_visible": false,
 "public_data_readable": true, "solution_writable": true}
```

The installer requires these observed results; `begin` repeats the probe before
dispatch. A script that prints the expected JSON without running those operations
does not verify isolation. The module supplies policy metadata to the existing
launcher and does not install or change that runtime.

Run the preview from the installing native task:

```sh
python3 run.py --config config.local.yaml install
```

Present the resolved draft count D, client count N, viewer port, knowledge base,
benchmark and isolation settings to the requester. After they confirm:

```sh
python3 run.py --config config.local.yaml install --confirm-config
python3 run.py --config config.local.yaml begin
```

The installation creates N query issues under the installing benchmark issue,
with one light babysitter per client and a shared solver with N task slots. Each
round gets a restored solution
repository and a new root thread. The supplied `dev` command creates a code child
with a `dev` label and copies the client's runtime policy onto it before launch.
The client reviews that child's changes and invokes `submit` from its query
issue. The command writes to that issue's current solution checkout regardless
of the shell's working directory. The controller freezes the submitted solution,
evaluates that artifact and registers its official score. It retains the frozen
artifact and score across interrupted handoffs. The shared journal serializes
concurrent reservations and submissions.

The loop hosts the tree in a background process when `port` is set. Open
`http://127.0.0.1:3003/` for the default configuration, or embed it as an iframe.
The server exits with the foreground controller. Run `serve` separately when you
want to inspect a stopped tree.

For a bounded live acceptance run, `begin --max-rounds 1` completes one scored
round per client. Its result contains the exact client issue IDs, round contexts
and completion counts. Missing submissions receive a short reminder in the same
round thread. A blocked or failed dev child wakes its query babysitter once per
changed child state. The babysitter owns its recovery. Query-client failures,
API errors and evaluator or runtime failures exit to the setup agent for repair.
Preserve the saved state and resume `begin` after reconciling
any interrupted write. Use a new state/tree/workdir path for a new installation.

Ctrl-C stops the controller and viewer. Native work already dispatched may still
be running; cancel its tasks in Multica before editing installed software.
