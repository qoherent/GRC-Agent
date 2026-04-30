# Vector Metadata Change Checklist

Use this checklist before changing `CATALOG_SEMANTIC_METADATA` or any indexed
catalog capability text. Vector metadata changes are exceptional evidence-based
maintenance, not routine eval tuning.

## Required Evidence

- [ ] Cluster count is at least 3, or misses repeat across at least 2 distinct sources.
- [ ] Misses are not ambiguous wording that should clarify or return multiple candidates.
- [ ] Misses are not an eval-expectation issue.
- [ ] The proposed capability remains true if the failing query did not exist.
- [ ] The proposed phrase describes stable block capability, not a one-off query phrase.
- [ ] At least one mutation-shaped negative trap is added or confirmed.
- [ ] Retrieval eval is rerun after the metadata change.
- [ ] Exact-ID misses remain 0.
- [ ] False-positive failures remain 0.
- [ ] Source-type misses remain 0.
- [ ] Documentation and baseline reports are updated when accepted.

## Required Review Fields

Every accepted metadata change must record:

- Block ID.
- Field changed.
- Stable capability reason.
- Supporting miss clusters.
- Queries it helps.
- Negative mutation-shaped trap.
- False-positive risk.
- Eval command and result.

## Rejections

Do not add metadata for:

- A single one-off query.
- Vague user goals such as "make it better" or "fix the graph".
- Mutation requests such as delete, save, insert args, repair plan, or raw YAML.
- Tutorial-derived recipes, default params, or transaction payloads.
- Cases where clarification or multiple candidates are safer than metadata.

Metadata proposal reports are advisory only. They must not automatically edit
code, docs, indexes, rankings, prompts, tool schemas, or runtime behavior.
