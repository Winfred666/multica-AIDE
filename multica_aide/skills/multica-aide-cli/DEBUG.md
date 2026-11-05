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
    test --> ended{Test exited?}
    ended -->|no: important log or time checkpoint| decision[Choose continue or exception]
    decision -->|continue| test
    decision -->|exception: end test| review
    ended -->|yes: success or error| review
    review -->|record: JSON delivery and one review paragraph| measured[Scored node]
    review -->|record: no scorable delivery| failed[Native block with review and logs]
    measured --> exit{Exit condition}
    exit -->|true| done
    exit -->|false| idea
```

The diamonds are scripted decisions. Test escalation supplies continue and exception.
Idea entry hands the candidate to the configured solver in a dev child, using the
workspace's existing native workdir helper. Native Multica owns its review and
acceptance.
The benchmark contract defines `validate(solution)`, `score(delivery)` and
`is_complete(delivery)`. The full JSON delivery is retained on the node; its numeric
projection selects future parents. Proposal and score registration record the
put-forward time and test duration. Initialization failure marks the client blocked
and sends the failed check to its parent. The official test runs in the background;
the monitor stays in the foreground and returns newly appended log excerpts.
The task's JSON record keeps its log offsets and 7.5/15/30/60-minute checkpoint
index; further checkpoints are hourly. Native child development follows its
ordinary delivery/block flow.

```mermaid
flowchart LR
    native[Native task] --> state[Task SOP state]
    state --> facts[Client checkpoint and shared tree]
    facts --> contract[Benchmark functions]
    contract --> option[Explicit recall]
    option --> save[Save node and return next state]
```
