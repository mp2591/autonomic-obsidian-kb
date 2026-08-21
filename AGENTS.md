# Agent instructions

This repository builds a token-economical knowledge infrastructure. Do not turn it into a broad RAG framework.

Before nontrivial work:

```bash
PYTHONPATH=src python -m autonomic_kb --vault examples/sample-vault --repo . context --budget 350
```

For a specific task, retrieve with an explicit budget and active paths. Use only returned context that is relevant. Do not dump the full vault. Read L3/L4 source notes only when a selected summary is insufficient or high-risk provenance is required.

After discovering a durable, reusable fact, submit it through `kb remember`; do not write an active note that bypasses promotion, security, provenance, and scope decisions.

Run before publishing:

```bash
PYTHONPATH=src python -m compileall -q src tests
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python benchmarks/run.py
```

Preserve Markdown as authority. Treat `.kb/index.sqlite3` as disposable. Never persist secrets. Never auto-resolve contradictions.
