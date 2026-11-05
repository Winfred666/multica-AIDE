# Installation protocol

```mermaid
flowchart LR
    prepare[Prepare benchmark and config] -->|prepare settings| confirm[Confirm configuration]
    confirm -->|install| installed[Clients installed]
    installed -->|start| running[Controller returned its measured result or setup blocker]
```

Preparation checks the official evaluator and benchmark contract. It uses the
existing native runtime. Installation creates only the feature's solver, client
issues and client SOP binding. The foreground controller starts the baseline client first;
all clients share the measured root and subsequent tree.

```mermaid
flowchart LR
    config[Local config] --> resources[Benchmark resources and contract]
    resources --> native[Native agents and query issues]
    native --> loop[Client SOPs and shared tree]
```
