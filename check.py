"""Run all quality checks: lint, format, type-check, tests.

Cross-platform substitute for `make check` — useful on Windows where
GNU Make is not installed by default. Exits non-zero on the first
failure so it can be wired into CI or a pre-push hook.
"""

from __future__ import annotations

import subprocess
import sys

COMMANDS: list[list[str]] = [
    ["ruff", "check", "src", "tests"],
    ["ruff", "format", "--check", "src", "tests"],
    ["mypy", "src"],
    ["pytest"],
]


def main() -> int:
    for cmd in COMMANDS:
        print(f"\n>>> {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(f"\n✗ {' '.join(cmd)} failed (exit code {result.returncode})")
            return result.returncode
    print("\n✓ All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())