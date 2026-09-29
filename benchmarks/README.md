# Benchmarks

`tasks.json` contains deterministic realistic retrieval tasks, active paths, budgets, and expected canonical IDs. `run.py` compares full-corpus/no-KB proxy cost with retrieval + injected context + maintenance proxy.

It is a regression gate. It writes `results/latest.json` (untracked) and fails when:

- aggregate savings are not positive;
- expected coverage collapses; or
- coverage, precision, false context, or injected tokens regress against the committed `results/reference.json`.

Regenerate the reference with `python benchmarks/run.py --update-reference` only in the commit that deliberately changes retrieval behavior. Benchmark retrieval ignores uncommitted working-tree changes, so results do not depend on the state of a developer's checkout.

The reference result is generated from the committed sample vault; it is not a claim about every model or repository. Use real agent token accounting and matched baselines for deployment decisions.
