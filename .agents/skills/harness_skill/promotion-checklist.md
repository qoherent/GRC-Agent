# Candidate Promotion Checklist

- [ ] Atomic edit(s) have explicit component + hypothesis.
- [ ] Shipped diff contains no benchmark/task-specific logic or grader exploit.
- [ ] Candidate and incumbent were evaluated on the same tasks/protocol.
- [ ] Failed/missing rollouts remain in the denominator.
- [ ] Score clears `S_best - delta`.
- [ ] Added cost is justified by gain, or within-band candidate materially reduces cost / tests a new structural mechanism.
- [ ] Behavioral metrics support the claimed mechanism (overflow, premature stop, tool misuse, verification, etc.).
- [ ] Domain/safety guards pass.
- [ ] Exact diff, score delta, cost delta, behavior delta, and decision are logged.
- [ ] Rollback snapshot exists before promotion.
- [ ] Held-out/OOD surfaces were not used to tune this candidate.
