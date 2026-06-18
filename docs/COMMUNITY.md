# Community

Everything you need to contribute, get help, and report problems.

## Contributing

Thanks for your interest in contributing! GRC Agent is an open-source local
LLM assistant for GNU Radio Companion `.grc` flowgraphs.

### Quick start (contributor path)

```bash
git clone https://github.com/qoherent/grc-agent
cd grc-agent
uv sync --locked --extra dev --extra gui
uv run pytest                  # GUI tests (pytest-qt)
uv run python -m unittest      # unit/integration tests
uv run ruff check .            # lint
uv run ruff format .           # format
```

### Project conventions

- **Python >= 3.12**, ruff-formatted, 100-column line length, type hints on
  all public functions.
- **GUI is the contract.** The PySide6 GUI in `src/grc_agent_gui/` is the
  only user-facing surface; add features to `src/grc_agent/` first as
  library logic, then wire the GUI second.
- **Safety first.** Mutations go through `change_graph`; the model is never
  given raw YAML or `grcc` access. See `AGENTS.md` for the safety contract.
- **No bypasses.** Don't add prompt folklore, regex-based routing, or
  fixture-specific shortcuts. Fix the authoritative data path.
- **Tests required for runtime changes.** Deterministic tests live under
  `tests/` and run under `python -m unittest`. GUI tests use `pytest-qt` and
  live under `tests/gui/`.

### Where to look

- `AGENTS.md` — internal contributor contract (mission, architecture,
  what not to do).
- `docs/superpowers/specs/` — dated design specs (ChatHistory refactor, inline
  model toolbar).
- `docs/MODEL_CONTEXT_BIBLE.md` — model-facing prompt + tool schema wrapper
  spec.
- `docs/CHANGELOG.md` — release history and the deferred harder-wins roadmap.

### Pull requests

1. Branch from `main`.
2. Keep PRs scoped: one feature, one fix, or one refactor.
3. Add or update tests for any behavior change.
4. Run `ruff check .` and `pytest` before opening.
5. Reference any related issue.

### Reporting issues

Use the [GitHub issue tracker](https://github.com/qoherent/grc-agent/issues).
For security issues, follow [Security](#security-policy) below instead of
opening a public issue.

## Code of Conduct

### Our Pledge

We as members, contributors, and leaders pledge to make participation in our
community a harassment-free experience for everyone, regardless of age, body
size, visible or invisible disability, ethnicity, sex characteristics, gender
identity and expression, level of experience, education, socio-economic
status, nationality, personal appearance, race, religion, or sexual identity
and orientation.

We pledge to act and interact in ways that contribute to an open, welcoming,
diverse, inclusive, and healthy community.

### Our Standards

Examples of behavior that contributes to a positive environment for our
community include:

- Demonstrating empathy and kindness toward other people
- Being respectful of differing opinions, viewpoints, and experiences
- Giving and gracefully accepting constructive feedback
- Accepting responsibility and apologizing to those affected by our mistakes,
  and learning from the experience
- Focusing on what is best for the community

Examples of unacceptable behavior include:

- The use of sexualized language or imagery, and sexual attention or
  advances of any kind
- Trolling, insulting or derogatory comments, and personal or political attacks
- Public or private harassment
- Publishing others' private information without their explicit permission
- Other conduct that could reasonably be considered inappropriate in a
  professional setting

### Enforcement Responsibilities

Community leaders are responsible for clarifying and enforcing our standards
of acceptable behavior and will take appropriate and fair corrective action
in response to any behavior that they deem inappropriate, threatening,
offensive, or harmful.

Community leaders have the right and responsibility to remove, edit, or
reject comments, commits, code, wiki edits, issues, and other contributions
that are not aligned to this Code of Conduct, and will communicate reasons
for moderation decisions when appropriate.

### Scope

This Code of Conduct applies within all community spaces, and also applies
when an individual is officially representing the community in public
spaces. Examples of representing our community include using an official
e-mail address, posting via an official social media account, or acting as
an appointed representative at an online or offline event.

### Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be
reported to the community leaders responsible for enforcement at
**grc-agent-conduct@qoherent.com** (replace with the address the maintainers
publish once the project is hosted). All complaints will be reviewed and
investigated promptly and fairly.

All community leaders are obligated to respect the privacy and security of
the reporter of any incident.

### Attribution

This Code of Conduct is adapted from the [Contributor Covenant][homepage],
version 2.1, available at
<https://www.contributor-covenant.org/version/2/1/code_of_conduct.html>.

[homepage]: https://www.contributor-covenant.org

For answers to common questions about this code of conduct, see the FAQ at
<https://www.contributor-covenant.org/faq>. Translations are available at
<https://www.contributor-covenant.org/translations>.

## Security Policy

### Supported versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

Until 1.0 ships, only the latest minor release receives security fixes.

### Reporting a vulnerability

Please **do not** open a public GitHub issue for suspected security
vulnerabilities.

Report privately by emailing **grc-agent-security@qoherent.com** (replace
with the address the maintainers publish once the project is hosted), or use
GitHub's [private vulnerability reporting][private-report] if the project is
hosted there.

[private-report]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

Include:

- A clear description of the issue and its impact.
- Reproduction steps (graph, prompt, environment).
- The commit/tag/branch affected.
- Any suggested fix or workaround.

We aim to acknowledge new reports within 3 business days and to ship a fix or
mitigation within 30 days for high-severity issues.

### Local-first scope

GRC Agent is a local-first tool: it does not phone home, does not require
network access for any core feature, and does not collect telemetry. The
default install path keeps every user artifact (chat history, vector index,
launcher state) under `~/.grc_agent/` and `~/.cache/grc_agent/`. Run
`python -c "from grc_agent.config import collect_package_paths; print(collect_package_paths())"`
for a full list.

If you find that any version of GRC Agent unexpectedly sends user data to a
remote endpoint, that is a security issue and should be reported as above.
