---
name: mandela
description: Audit an evaluation, metric, benchmark, experiment, A/B test, holdout, or validation plan for circular confirmation and leakage. Use before trusting how success will be measured, when a result looks suspiciously clean, or when the model, scorer, designer, dataset, or labelers may not be independent. This Skill is read-only. Do not use for verifying ordinary factual claims against sources, implementing tests, general code review, performance tuning, or choosing a model.
---

# Mandela

Test whether independent ground truth enters the validation or whether its
components are only confirming a result they jointly created.

## Audit

1. Name the decision the validation is meant to support.
2. Identify the model or subject, designer, scorer, dataset, labels, holdout,
   collection procedure, and claimed ground truth. Mark missing evidence
   `unknown`; do not infer it.
3. Draw the evidence path from an independent observation to the final verdict.
4. Read [leakage-patterns.md](references/leakage-patterns.md) and test every
   applicable pattern. Report only patterns supported by evidence.
5. For every hit, give the smallest independence repair and the new evidence
   that repair would produce.
6. Test the audit itself: could a fresh reviewer reach the verdict from cited
   artifacts without inheriting the designer's conclusions?

## Output

```text
Decision:
Validation components:
Independent ground truth:
Leakage findings:
  - pattern
  - evidence
  - impact
  - independence repair
Unknowns:
Verdict: credible | conditionally credible | not independent | insufficient evidence
```

Remain read-only. Do not rewrite the experiment, generate favorable results,
or treat model agreement as external truth. A clean audit returns no findings
rather than inventing risk.

Keep evaluation designs, prompts, results, datasets, and reports inside
approved local or internal systems. External research is not required by this
Skill. If a source must be checked, use the Hub's approved source workflow and
never place confidential terms in a public query.

If the request is about facts, tests, code quality, or model selection rather
than validation independence, otherwise do not apply this Skill.
