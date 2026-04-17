#!/usr/bin/env python3
"""Verify that the local machine matches the project's pinned assumptions."""

from grc_agent.doctor import print_doctor_report, run_doctor


def main() -> int:
    report = run_doctor(check_retrieval=False)
    print_doctor_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
