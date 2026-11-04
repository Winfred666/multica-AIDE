# Client protocol

```mermaid
flowchart TD
    init[Initialize] --> checks{Integrity and existing deliveries}
    checks -->|missing requirement| blocked[Block and notify parent]
    checks -->|exit condition met| done[Finished with existing node]
    checks -->|no baseline| idea[Idea: empty candidate]
    checks -->|untested baseline| test[Test]
    checks -->|measured root| idea
    idea -->|test: one description paragraph| test
    test -->|record: JSON delivery and one review paragraph| measured[Scored node]
    measured --> exit{Exit condition}
    exit -->|true| done
    exit -->|false| idea
```

The diamonds are scripted decisions. Each interaction state supplies one option.
The benchmark contract defines `validate(solution)`, `score(delivery)` and
`is_complete(delivery)`. The full JSON delivery is retained on the node; its numeric
projection selects future parents. Proposal and score registration record the
put-forward time and test duration. Initialization failure marks the client blocked
and sends the failed check to its parent.

```mermaid
flowchart LR
    native[Native task] --> state[Task SOP state]
    state --> facts[Client checkpoint and shared tree]
    facts --> contract[Benchmark functions]
    contract --> option[One valid option]
    option --> save[Save node and return next state]
```
