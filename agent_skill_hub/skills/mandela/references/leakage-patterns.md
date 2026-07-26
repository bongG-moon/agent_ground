# Validation leakage patterns

Apply all relevant patterns and cite the concrete component or artifact that
causes each hit.

| Pattern | Failure signal | Independence repair |
| --- | --- | --- |
| Recall, not reason | The tested system has already seen or memorized the answer | Use unseen instances with controlled provenance |
| Wrong null hypothesis | The ablation removes a label but leaves the usable signal | Remove the mechanism-level signal or design a real negative control |
| Shared hallucination | Two model components confirm one another without outside observation | Add independently produced labels or measurements |
| Tautology | The scorer grades categories or rules it created | Separate rubric design from blinded scoring |
| Verifier equals designer | The same actor creates the method and certifies its success | Use an independent, reproducible verifier |
| Shared-pool bias | Train and holdout inherit the same labeler or collection bias | Split labeler pools, sources, time, or sites |
| Frame injection | The prompt hands the subject the hypothesis or desired answer | Blind or neutralize the framing |
| Demand characteristics | Subjects change behavior because they know the measured outcome | Blind participants or use unobtrusive measurement |

## Evidence questions

- Who produced the claimed truth, and could they see the hypothesis?
- Could the scorer infer the expected result or condition?
- Were examples, labels, prompts, or collection recipes reused across splits?
- Is the holdout independent by source and procedure, not just row identity?
- Can the result be reproduced without private designer knowledge?
- What observation could prove the result wrong?

## Severity

- **Critical**: no independent ground truth enters; the validation is circular.
- **High**: the claimed comparison is materially contaminated.
- **Medium**: independence exists but an important bias is shared.
- **Low**: reporting or blinding weakness that does not yet overturn the result.

Do not inflate severity from possibility alone. Use `insufficient evidence`
when the artifacts cannot establish whether a pattern fires.
