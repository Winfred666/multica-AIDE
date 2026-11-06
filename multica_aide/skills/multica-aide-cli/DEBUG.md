# Client protocol

```mermaid
flowchart TD
    initialize --> checks{Integrity and saved progress}
    checks -->|missing requirement: notify parent| needs_setup
    checks -->|exit met| finished
    checks -->|another client must establish root| waiting
    waiting -->|check| initialize
    checks -->|draft or refine| idea
    checks -->|baseline or pending test| test
    checks -->|test already ended| review
    checks -->|round already scored| tested
    idea -->|test: one description paragraph| test
    idea -->|separate: develop| developing
    developing -->|continue: delivery and cleanup pending| developing
    developing -->|continue: validated delivery format| test
    test -->|continue: running| test
    test -->|ended or exception| review
    review -->|record: JSON and review paragraph| tested
    review -->|record: no scorable delivery| test_failed
    tested -->|continue| exit{Exit met?}
    exit -->|yes| finished
    exit -->|no| idea
```

The benchmark supplies `validate(solution)`, `score(delivery)` and
`is_complete(delivery)`. Nodes retain the full delivery, put-forward time and test
duration; the numeric score ranks parents. Each node retains one worktree and its
Git commit. Drafts branch from the prepared template, refinements from the selected
node's commit. Separate mode reuses the developer child created at installation.

State: `test`

```mermaid
flowchart TD
    enter[Test keeps running and logging in background] --> ended{Process ended?}
    ended -->|yes: success or error| result[Save exit and any scorable JSON]
    result --> review["Next: review"]
    ended -->|no| trigger{New WARNING/ERROR or checkpoint?}
    trigger -->|no| ended
    trigger -->|yes| progress[Return new log excerpt and paths; save cursor in task JSON]
    progress --> recall{Recall in same turn}
    recall -->|continue| ended
    recall -->|exception| stop[Stop experiment process group]
    stop --> result
    checkpoints["7.5 / 15 / 30 / 60 min, then hourly"] -.-> trigger
```

State: `review`

```mermaid
flowchart TD
    record["record: one review paragraph"] --> delivery{Scorable recorded delivery?}
    delivery -->|no| failure[Save review and logs; leave node unscored]
    failure --> blocked["Next: test_failed; report native block"]
    delivery -->|yes| match{Submitted JSON equals recorded JSON?}
    match -->|no| stay["Stay: review; correct delivery"]
    match -->|yes| score[Verify tested commit; save score and review]
    score --> tested["Next: tested"]
```
