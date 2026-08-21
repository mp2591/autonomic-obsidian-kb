# Benchmarks

`tasks.json` contains deterministic realistic retrieval tasks, active paths, budgets, and expected canonical IDs. `run.py` compares full-corpus/no-KB proxy cost with retrieval + injected context + maintenance proxy and fails when the aggregate does not save tokens or expected coverage collapses.

The reference result is generated from the committed sample vault; it is not a claim about every model or repository. Use real agent token accounting and matched baselines for deployment decisions.
