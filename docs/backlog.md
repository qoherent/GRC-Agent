# Product backlog

Only unfinished work belongs here. Shipped features and completed fixes are recorded once in [the changelog](../CHANGELOG.md); current operation is described in [the technical overview](technical_overview.md) and [GRC interaction guide](grc-interaction.md).

## Observation and media

### Vision capability discovery

Detect image-input support from authoritative provider metadata or an explicit capability probe and expose it in Settings. Qualify the existing `plot` and `canvas` modes against actual vision-capable endpoints; do not infer support from a model name or an invented profile field.

### Additional capture formats and DSP analysis

Extend observation beyond raw native File Sink output where needed: metadata-bearing captures, explicit rate propagation, and additional observation points. Qualify branched graphs, real SDR hardware, and more signal-processing tasks. Add spectral-density, eye, or waterfall plots only with defined numerical conventions and independent acceptance cases. SNR, symbol-error rate, and similar estimates need task-specific definitions; do not manufacture generic correctness scores.

### Additional project media

- SVG rasterization or explicit text fallback.
- Structured CSV, TSV, and spreadsheet inspection with column metadata and requested row ranges.
- Additional numerical summaries, such as quantiles or peak tables, where they improve verification without hiding sample selection or omissions.

## Offline RF assets

Build a curated catalog of small SigMF recordings for demodulator and decoder testing without SDR hardware. Preserve sample rate, center frequency, datatype, provenance, license, and download size. Download assets on demand into the project, and use their metadata to configure the graph through normal approved edits.

## Project-document retrieval

Add project-scoped retrieval for large datasheets, hardware manuals, PDFs, protocol documents, and code archives. Reuse local embedding and SQLite vector/FTS infrastructure. Keep project content in the project sandbox, and return attributable excerpts with explicit omissions rather than entire documents.

## Knowledge coverage and retrieval quality

- Review coverage and retrieval quality for Qt GUI sinks, hardware setup, synchronization, framing, and modulation against the current bundled corpus.
- Measure catalog/docs result density, lexical fallback, truncation disclosures, and ranking quality using representative queries.
- Measure default tool-schema/context cost and stacked transport/SDK retries before changing capability exposure or retry ownership. Preserve advanced capabilities and compare on fixed tasks and configured models.

## Platform and upstream follow-up

Reproduce these observations against the pinned dependency and current upstream implementation before filing or implementing a fix:

- Shell commands waiting on an unclosed stdin pipe; propose an explicit closed stdin where appropriate.
- Prompt-injection false positives on ordinary documentation JavaScript; preserve the local detect-and-log behavior while evaluating upstream improvements.
- Recovery of interrupted conversation snapshots during history search.
- Deferred approvals receiving original rather than validated tool arguments; the application already repairs display arguments locally.
- Retryable tool errors leaving persistence effects without a terminal event.
- GNU Radio packages that fail to publish the spawned process to `page.process`, breaking native Stop. Track the package fix and qualify native process ownership when upgrading GNU Radio.

## Remaining sidebar and test maintenance

- Move demonstrated blocking SQLite reads off the unified UI loop while preserving transaction ordering and session lifecycle. The HTTP context probe already runs off-loop.
- Split oversized test modules, prefer shared sidebar fixtures and public/widget behavior, and review complexity suppressions. Retain the existing goal of test modules below 1,500 lines without weakening behavioral assertions.

## Reopen chats from unsaved graphs

Make pathless sessions reachable from the welcome/recent view, either through first-class pathless identity or by binding the session on first save. Preserve graph identity: an unsaved tab must not be resolved into a fabricated filesystem path. Cover listing, loading, first save, renaming, and session deletion.
