#!/usr/bin/env python3
import shutil
import sys


EXPECTED_PYTHON = (3, 12)
EXPECTED_GNURADIO = "3.10.9.2"


def print_result(name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")


def check_python() -> bool:
    version = sys.version_info
    ok = (version.major, version.minor) == EXPECTED_PYTHON
    detail = f"{version.major}.{version.minor}.{version.micro} (expected {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]}.x)"
    print_result("Python version", ok, detail)
    return ok


def check_grcc() -> bool:
    grcc_path = shutil.which("grcc")
    ok = grcc_path is not None
    detail = grcc_path if grcc_path else "grcc not found on PATH"
    print_result("grcc on PATH", ok, detail)
    return ok


def check_gnuradio_import() -> bool:
    try:
        from gnuradio import gr
        version = gr.version()

        ok = version == EXPECTED_GNURADIO
        detail = f"{version} (expected {EXPECTED_GNURADIO})"
        print_result("GNU Radio import/version", ok, detail)
        return ok
    except Exception as exc:
        print_result("GNU Radio import/version", False, str(exc))
        return False


def main() -> int:
    print("Checking local environment...\n")

    results = [
        check_python(),
        check_grcc(),
        check_gnuradio_import(),
    ]

    all_ok = all(results)
    print()
    print("Environment OK" if all_ok else "Environment check failed")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())