---
name: hermes-subagent
description: Call Hermes Agent as an online research subagent (packages, features, system design). Never for local coding.
version: 2.0.0
author: Mahmoud Sallam (mahmoudsallam)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hermes, subagent, research, cli, orchestration, system-design]
---

# Hermes as an Online Research Subagent

Main agents delegate ONLY online research to Hermes: current package versions/features, library comparisons, system design patterns, "is this approach outdated?". Hermes never writes local code and never edits this repo. It researches the web and returns findings.

## Persona (verbatim, prepend to every call)

The subagent runs as a senior system designer: never writes code, focuses on high-level architecture, flow, and robustness. Replies are concise, fluff-free, grounded in the most recent packages/features/approaches. It must catch and call out outdated approaches or bad logic, without bias, based on grounded searches; ask for more details when needed and never assume; reject ad-hoc logic that adds redundancy, cost, latency, or worse performance; be bold, objective, grounded; lean toward simplification; stop and ask questions before major decisions (framework, library, model choice). Use Context7 MCP for library syntax (versions are very new). Always cite sources (URLs) for claims.

Full persona text lives in `research-persona.md` next to this file.

## One-Shot Research (default)

```bash
hermes chat -Q -t web,browser \
  -q "<persona summary>. Research: <task>. Cite URLs. Reply concisely."
```

- `-Q` quiet mode: stdout is ONLY the final answer (parse programmatically).
- `-t web,browser`: research toolsets only. Do NOT enable terminal/file/memory — this subagent must not touch the local system or persist state.
- Put ALL context in the query; it knows nothing of your conversation.
- Typical research runs 2-10 min: set timeout 600s, or cap with `--max-turns 40 --run-budget 300`.

## Multi-Line Prompt (persona file + task)

```bash
{ cat /path/to/.agents/skills/hermes-subagent/research-persona.md
  echo; echo "TASK: <your research question>"; } > /tmp/q.md
hermes chat -Q -t web,browser --query-file /tmp/q.md --run-budget 300
```

`--query-file` is shell-safe: quotes, `$(...)`, backticks pass verbatim.

## When Hermes Asks Back

The persona forbids assumptions. One-shot mode cannot get an answer back, so:

1. Read the output: if it contains clarifying questions instead of findings, answer them.
2. Resume the same session: `hermes chat -Q -t web,browser -r <session-id> -q "<answers>".`
3. Session ID is printed at the end of `-Q` output; with tmux, just `send-keys` the answer.

## Long / Steered Research (tmux)

```bash
tmux new-session -d -s research -x 120 -y 40 'hermes -t web,browser'
tmux send-keys -t research 'Compare queue options for our pipeline; call out outdated choices' Enter
tmux capture-pane -t research -p | tail -40
tmux kill-session -t research
```

## Interactive Decision Points

The persona stops before committing to a framework/lib/model. Two options:
- Pre-decide in the query: "We chose X because Y; evaluate it, do not re-litigate."
- Let it ask, then resume per the pattern above.

## Context7 MCP (one-time setup)

Not configured by default. To let the subagent verify brand-new library syntax:

```bash
hermes mcp add context7 --url https://mcp.context7.com/mcp
```

Then add `context7` MCP tools to research calls. Skip if the research question needs no API syntax.

## Rules

- Research only: findings, comparisons, recommendations, URLs. No code writes, no repo edits, no worktrees needed.
- Verify before acting: cross-check critical claims (versions, benchmarks) yourself or with a second query; subagent claims are not facts.
- Recurring/scheduled research -> use a cron job, not a persistent Hermes process.