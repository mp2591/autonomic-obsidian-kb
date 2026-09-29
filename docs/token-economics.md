# Token and task economics

> V3 implementation status and limitations are specified in [V3 implementation](v3-implementation.md). This document retains design context; it is not a claim of measured agent savings.

The objective is expected total task utility, not retrieval recall.

```text
C_task = C_retrieval + C_context + C_search + C_repeated_reasoning
       + C_tools + C_latency + C_correction + C_risk + amortized C_maintenance

net_savings = C_no-KB - C_KB
```

A short but unreliable memory can be more expensive than a longer verified one because its effective cost includes expected correction and unsafe-action penalties.

## Measurement layers

- deterministic proxy benchmark for fast regression;
- actual retrieval/context tokens;
- agent searches, file reads, commands, retries and corrections;
- latency and task success;
- stale/unsafe retrievals;
- durable memory maintenance cost;
- paired no-KB versus KB task outcomes;
- shadow-route comparisons and rank-example feedback.

`kb outcome`, `kb benchmark --traces`, `kb replay`, `kb shadow`, and `kb calibrate` provide the measurement/control path. Frequency of retrieval is not treated as truth. Incorrect, stale, correction-causing or unsafe memories contribute negative labels.

A new default feature must improve success or preserve success while lowering total cost, with bounded false-context and safety rates.
