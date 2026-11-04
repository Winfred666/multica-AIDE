# multica-AIDE

A small AIDE search module for Multica. Each installation owns a scored baseline,
a shared solution tree, and independent client sessions. Clients draft solutions,
then refine the best measured node. The search follows the draft and improvement
loop in [Weco's AIDE](https://github.com/wecoai/aideml).
Standalone clients use light babysitters for round control and blocked-child
recovery, with a shared `gpt-6.1-terra` solver for dev work. Configure
`solver_model` and `cli_babysitter_model` for the available runtime models.

A node contains `id`, `title`, `description`, numeric `score`, optional `review`,
lineage and an artifact reference. The module rejects missing and nonfinite
scores. Outer applications own benchmark evaluation, artifact restoration and
phase ordering.

Install Python 3.11+ and the package:

```sh
python3 -m pip install -e .
```

You can also run `python3 /path/to/multica-AIDE/run.py` from a clone with PyYAML
installed. Credentials come from the current native task's `MULTICA_*` environment.

- [Install in a Multica issue](docs/installation.md): run N query clients against one benchmark project.
- [Embed in another module](docs/integration.md): call tree methods between your own phases and embed the HTML widget.
- [Prepare a benchmark](skills/multica-create-bench/SKILL.md): define public inputs, hidden evaluation and official scoring.

`config.yaml` supplies the installation settings. `install` previews the resolved
configuration before `--confirm-config` creates resources. Generated trees,
solutions and local configuration stay outside version control.
