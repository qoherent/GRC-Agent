---
title: TUN/TAP Privilege Failure Guidance - Plan
type: fix
date: 2026-08-31
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# TUN/TAP Privilege Failure Guidance - Plan

## Goal Capsule

- **Objective:** A user whose flowgraph fails to allocate a TUN/TAP interface (`network_tuntap_pdu` → `tun_alloc` EPERM, missing CAP_NET_ADMIN) gets correct, safe, self-service remediation from the agent in chat and from the README — without elevating the desktop app or its interpreter.
- **Means:** Extend the established unobservable-platform-quirk pattern (system-prompt bullet + README permissions bullet, the SDR USB/udev precedent) with a TUN/TAP privilege entry (KTD1).
- **Authority:** AGENTS.md governs conflicts — prompts carry only unobservable harness contracts and GRC platform quirks; zero ad-hoc heuristics (no log-detection machinery); GUI-only (no CLI diagnostics); never bump the version number. Repo test and ruff gates arbitrate completion.
- **Stop conditions:** Prompt, prompt-content tests, README, and CHANGELOG are done and gates are green. Stop and re-scope if the correct remediation turns out to be observable to the model already (then the prompt entry is unnecessary).
- **Execution profile:** Two units, single pass, no sequencing risk.

## Product Contract

### Summary

Add a TUN/TAP privilege-failure quirk to the executor agent's system prompt ("Execution & Diagnostics" block, beside the SDR USB permissions line) and a matching README troubleshooting bullet. The guidance names the root cause (creating a named tun/tap interface requires CAP_NET_ADMIN; a world-accessible `/dev/net/tun` is not sufficient), gives the one-time safe remediation (user pre-creates a persistent TAP owned by their own user, outside the app), warns against root and setcap, and states the reboot boundary.

### Problem Frame

A real user (non-root `qrf`, uid 1000) ran an SDR flowgraph bridging RF to a network interface via `network_tuntap_pdu`. The run failed at `tun_alloc('tap0')` with EPERM even though `/dev/net/tun` was world-accessible — the kernel requires CAP_NET_ADMIN to create a named interface, and device-node permissions do not substitute for it (verified in `drivers/net/tun.c`: attach to an existing persistent TAP owned by the caller's euid needs no capability; creation does). The flowgraph was structurally valid and the agent diagnosed this correctly from `get_run_log`, but listed "run as root" and "setcap the interpreter" as peer options. For a GUI desktop app with an RF-safety approval model and a standing no-sudo posture for SDR hardware, those options are unsafe or unactionable. The attach/create asymmetry is knowledge the model cannot observe from the harness; the established home for such platform quirks is the system prompt, mirrored in the README for humans.

### Requirements

**Agent guidance**

- R1. When run logs show a TUN/TAP allocation failure (`tun_alloc`, `TUNSETIFF`, or EPERM/Operation not permitted allocating a tun/tap interface), the executor agent attributes it to missing CAP_NET_ADMIN, states the flowgraph is otherwise valid, and advises the one-time remediation: pre-create a persistent TAP owned by the desktop user, run by the user outside the app.
- R2. That guidance explicitly warns against running the app or its interpreter as root and against `setcap`-ing the interpreter, matching the existing no-sudo posture for SDR USB errors.
- R3. That guidance states the persistence boundary: the pre-created interface survives flowgraph restarts but not reboots, so it must be re-created once per boot or made durable with a system service.

**Docs and scope**

- R4. The README carries a human-facing TUN/TAP permissions bullet beside the existing SDR hardware permissions guidance, with the same remediation.
- R5. The fix adds guidance text only — no log-pattern detection or error classification, no privilege handling, and no shell execution of interface-creation commands by the app.

### Key Decisions

- KD1. Remediation posture: pre-created persistent owned TAP is the primary advice; root and setcap are explicitly discouraged rather than listed neutrally. (session-settled: user-approved — chosen over listing all three fixes neutrally: options presented as peers get tried, and whole-app elevation contradicts the app's no-sudo SDR posture.) Governs R1, R2, R3.

### Success Criteria

- A user hitting the reported failure receives, in chat, the root cause, the exact one-time command, and the reboot caveat without a web search; the README carries the same at setup time.
- The system prompt still names no tools and adds no per-scenario command folklore beyond the single remediation command, matching the SDR USB entry's precedent.

### Scope Boundaries

- Out of scope: automatic classification of run-log errors (zero-heuristics rule); app-side privilege elevation or sudo execution (sandbox and no-sudo rules); changes to GNU Radio or gr-network blocks; udev/systemd unit authoring features.
- Deferred to Follow-Up Work: adding a TUNTAP_PDU page to the offline knowledge corpus (`docs/wiki_gnuradio_org/`) so `query_knowledge` can ground the block's `ifname` semantics; belongs to the corpus-expansion backlog track.

## Planning Contract

### Key Technical Decisions

- KTD1. Fix surface: one prompt bullet in the "Execution & Diagnostics" block of `src/grc_agent/prompts.py` plus one README bullet — extending the established SDR USB/udev precedent (unobservable environment quirk → condition + correct advice). Chosen over automatic log-based detection: AGENTS.md's zero-ad-hoc-heuristics and fix-at-source rules rule out regex classification of run output, and the agent already diagnoses this failure class correctly from `get_run_log`. (session-settled: user-approved — chosen over detection machinery: heuristics violate repo rules and the diagnosis is already sound; only the advice was wrong.)
- KTD2. Remediation content (instantiates KD1; R1–R3): primary advice is a one-time `sudo ip tuntap add dev tap0 mode tap user <user>` run by the user outside the app. The kernel's attach/create asymmetry — `tun_set_iff()` in `drivers/net/tun.c` allows attaching to an existing persistent TAP owned by the caller's euid without CAP_NET_ADMIN, while creating a named interface requires it — makes subsequent flowgraph runs work unprivileged; `ip tuntap add` performs create + `TUNSETOWNER` + `TUNSETPERSIST` (iproute2 `iptuntap.c`). setcap is discouraged: it grants CAP_NET_ADMIN to every script the interpreter runs, and silently fails on nosuid mounts and interpreter churn. The interface does not survive reboot (`IFF_PERSIST` outlives the fd, not the kernel). (session-settled: user-approved — chosen over neutral listing: unsafe options presented neutrally get tried first.)
- KTD3. Prompt shape: mirror the SDR USB line's condition → advice form, phrased generically over the failure signature (TUN/TAP allocation + EPERM), not per-block special cases — one uniform rule applied to all cases.

## Implementation Units

### U1. TUN/TAP privilege quirk in the executor system prompt

- **Goal:** The executor agent gives the grounded, safe remediation for TUN/TAP CAP_NET_ADMIN failures.
- **Requirements:** R1, R2, R3, R5
- **Dependencies:** none
- **Files:** `src/grc_agent/prompts.py`, `tests/test_isolation.py`, `CHANGELOG.md`
- **Approach:**
  1. Add one bullet to the "Execution & Diagnostics" block, adjacent to the SDR USB line, mirroring its shape (KTD1, KTD3).
  2. Bullet content per KD1/KTD2: failure signature → root cause (creating a named tun/tap interface needs CAP_NET_ADMIN; world-accessible `/dev/net/tun` is not sufficient) → remediation (user runs `sudo ip tuntap add dev tap0 mode tap user <user>` once outside the app; flowgraphs then attach unprivileged) → warn against running the app as root or setcap-ing the interpreter → reboot caveat.
  3. Extend the fragments tuple in `test_system_prompt_keeps_unobservable_contracts` to assert the new guidance and the existing SDR USB line.
  4. Record a CHANGELOG entry under `[Unreleased]`.
- **Patterns to follow:** the SDR USB permissions bullet in the same prompt block; `test_system_prompt_keeps_unobservable_contracts` in `tests/test_isolation.py` for the fragment assertions.
- **Test scenarios:**
  - `build_system_prompt()` contains the TUN/TAP guidance naming `CAP_NET_ADMIN` and `ip tuntap add`.
  - The SDR USB permissions fragment is asserted in the same tuple, so a future prompt-streamlining pass cannot silently drop either environment-quirk rule.
  - The discouragement clause (not running the app as root; not setcap-ing the interpreter) is present.
  - `build_planner_prompt()` output is unchanged — the planner keeps its read-only planning role.
- **Verification:** Unit suite and ruff green; the prompt diff shows one new bullet, no tool names, and no unrelated prompt churn.

### U2. README TUN/TAP permissions guidance

- **Goal:** Humans setting up SDR-to-network flowgraphs find the same remediation in the README, before or after a failure.
- **Requirements:** R4 (carrying R1–R3 content for humans)
- **Dependencies:** U1 (terminology parity only)
- **Files:** `README.md`, `CHANGELOG.md`
- **Approach:** Add a "TUN/TAP interfaces" bullet next to "SDR hardware permissions": non-root flowgraphs cannot create named tun/tap interfaces; one-time `sudo ip tuntap add dev tap0 mode tap user $USER`; not persistent across reboots; do not run the app with sudo or setcap the interpreter. Add a CHANGELOG entry under `[Unreleased]`.
- **Test expectation:** none — documentation-only change; the repo gate still runs.
- **Verification:** README renders correctly and the command matches U1's prompt wording.

## Verification Contract

| Gate | Command / check | Applies to |
|---|---|---|
| Fast unit tests | `uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py` | U1, U2 |
| Lint | `uv run ruff check` | U1, U2 |
| Prompt inspection | Render `build_system_prompt()` and read the new bullet against the AGENTS.md prompt charter | U1 |

## Definition of Done

- **Global:** Both gates green; the prompt carries the TUN/TAP quirk with fragments asserted in `tests/test_isolation.py`; the README bullet is present; CHANGELOG `[Unreleased]` entries added; no version bump in `pyproject.toml`, `CITATION.cff`, or `CHANGELOG.md`; the diff contains no detection, classification, or privilege-handling code (R5).
- **Cleanup:** No alternate prompt wordings or scratch variants are left in the diff; the change is one bullet per surface.
