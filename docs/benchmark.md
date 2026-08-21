# Benchmark design

## Question

Does the KB reduce the total context/search burden while still returning the small set of memories required by realistic tasks?

## Compared workflows

1. **No-KB proxy:** an agent reads/searches the relevant corpus at full-note granularity.
2. **KB-assisted:** task query cost + selected progressive layers + amortized maintenance proxy.

The benchmark reports estimated tokens, net savings, reduction, precision among injected memories, and coverage of expected canonical IDs.

## Why it is a proxy

An offline harness cannot perfectly know how much reasoning or searching a specific model would perform. Results are therefore labeled deterministic proxy estimates. They are useful for regression and architecture comparisons, not for claiming universal model savings.

## Guardrails

A change should not be accepted solely because it raises recall. It should improve or preserve:

- net token reduction;
- expected-memory coverage;
- precision of injected context;
- stale/unsafe exclusion;
- deterministic repeatability.

The sample suite covers repository orientation, test commands, architecture decisions, and a debugging failure/solution pair.

## Run

```bash
PYTHONPATH=src python benchmarks/run.py
# or
kb --vault examples/sample-vault benchmark \
  --tasks benchmarks/tasks.json \
  --output benchmarks/results/reference.json
```

## Production measurement

Live agents should record a no-KB matched baseline or an A/B cohort, actual model token accounting, repository searches after context injection, task completion/correction, and maintenance work. Never compare different tokenizers or model/tool policies without normalization.
