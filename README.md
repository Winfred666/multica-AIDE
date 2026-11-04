# multica-AIDE

A shared solution tree for Multica optimization. Clients draft independent
solutions, then refine the best measured node, following
[Weco's AIDE](https://github.com/wecoai/aideml). Each node records its ID, title,
description, finite score, optional review and artifact reference.

Use it in either form:

- **Native campaign:** an installation script guides benchmark preparation;
  the `AIDE_cli` round protocol coordinates N light query clients, a shared solver
  and official host scoring. Native Multica supplies solution and review workflows.
- **Embedded module:** an outer application controls restoration, evaluation and
  phase ordering through the Python API. Independent trees can optimize an outer
  solution and its SOPs in the same application.

Both use the same embeddable HTML tree. Concurrent client sessions share measured
results and keep their own pending rounds.

Install Python 3.11+ and the package:

```sh
python3 -m pip install -e .
```

A clone also supports `python3 run.py` with PyYAML installed. Read
[AGENTS.md](AGENTS.md) for [native installation](AGENTS.md#native-installation)
or [embedding](AGENTS.md#embedding).

[config.yaml](config.yaml) defaults to five drafts, two clients, port 3003,
no knowledge base, `solver_model: gpt-6.1-terra` and
`cli_babysitter_model: gpt-6-luna`. Set the benchmark, runtime and host evaluator
for each installation. Local configuration, solution artifacts and journals stay
outside version control.
