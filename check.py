import subprocess
import sys

commands = [
    ["ruff", "check", "src", "tests"],
    ["ruff", "format", "--check", "src", "tests"],
    ["mypy", "src"],
    ["pytest"]
]

for cmd in commands:
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)