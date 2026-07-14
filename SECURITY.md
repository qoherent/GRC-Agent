# Security Policy

## Reporting a vulnerability

Please do not open a public GitHub issue for a security vulnerability.

Instead, use GitHub's private vulnerability reporting: open the
[Security tab](https://github.com/qoherent/grc-agent/security) on this
repository and click "Report a vulnerability." This opens a private
advisory visible only to maintainers until a fix is ready.

## Scope notes

This is a local-first desktop tool: the web dashboard binds to
`127.0.0.1` by default and is meant to be run on a single user's own
machine, not exposed to a network. `POST /grc/apikey` writes API keys
to a local `.env` file in plaintext (the same convention `.env` files
use generally) — treat that file with the same care as any other
credential store on your machine.
