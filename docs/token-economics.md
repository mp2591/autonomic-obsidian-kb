# Token economics

## Objective

The system minimizes expected task cost rather than maximizing retrieval recall:

```text
C_task = C_retrieval + C_context + C_discovery + C_reasoning-repeat + C_recovery + C_maintenance
net_savings = C_no-KB - C_KB-assisted - amortized C_maintenance
```

A retrieved note is valuable only when its expected avoided work exceeds the tokens and risk it introduces.

## Waste model

| Waste source | Control |
|---|---|
| Repeated repository search | repository/file maps and commands |
| Rereading understood files | layered summaries with path invalidation |
| Rediscovering architecture | verified architecture and invariant memories |
| Repeating debugging | known-failure + solution pairs |
| Regenerating commands | concise command/workflow memories |
| Dependency rediscovery | dependency behavior with version triggers |
| Irrelevant context | scope/trust gates and minimum score |
| Oversized context | L0–L4 disclosure and hard budget |
| Stale memory recovery | validation, invalidation, confidence decay |
| Duplicate memories | canonical IDs, duplicate detection, compaction |
| Multi-agent duplicate work | shared repository/project scopes |
| Global pollution | explicit scope lattice and repo fingerprints |

## Candidate-memory economics

`LearningCandidate.score()` combines:

- reuse likelihood: 19%;
- rediscovery cost: 19%;
- confidence: 18%;
- stability: 13%;
- uniqueness: 11%;
- token-savings potential: 20%;
- maintenance-cost penalty: 8%.

Candidates below the promotion threshold remain in the inbox. Security findings override economics and route to quarantine.

## Retrieval score

The implemented score weights are:

| Signal | Weight |
|---|---:|
| Lexical relevance | 30% |
| Scope applicability | 17% |
| Active path match | 12% |
| Task-type affinity | 10% |
| Confidence | 10% |
| Authority | 8% |
| Validation state | 6% |
| Freshness | 4% |
| Historical utility | 3% |

Status, contradictions, scope mismatch, and trust are gates or penalties rather than weak ranking features. A module note without an active module/path match is excluded even if its text is similar.

## Budget allocation

The budget includes a small manifest reserve. Each candidate is first represented at an automatically chosen layer. If it does not fit, the allocator attempts a lower layer. After at least one result, retrieval stops when score-per-token falls below the marginal threshold.

The algorithm therefore prefers three precise one-line facts over one broad design document when both solve the task.

## Measurement

Tracked signals include:

- estimated retrieval and injected-context tokens;
- selected items and layers;
- inclusion/exclusion reasons;
- retrieval count and item reuse;
- validation, contradiction, and stale-memory rates;
- benchmark baseline versus assisted tokens;
- maintenance proxy cost.

Character/word token estimates are intentionally model-independent. Production deployments can add a model tokenizer, but comparisons must use one tokenizer consistently.

## Avoided-token attribution

Exact “reasoning avoided” is counterfactual. The benchmark therefore separates directly measured context size from estimated baseline file-reading/search cost and labels the result as a proxy. Live integrations should record task completion, agent searches after retrieval, user corrections, and whether a retrieved memory was cited or expanded. These observations allow posterior utility updates without pretending the counterfactual is exact.
