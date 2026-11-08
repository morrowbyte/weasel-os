#!/usr/bin/env python3
"""Fail closed on unexpected runner provenance; retain a small CI receipt.

This is a routing guard, not a cryptographic attestation. The independent
acceptance check also matches the GitHub job ID to Boat's CI API sandbox ID.
"""

import json
import os
from pathlib import Path
import re
import subprocess


def main():
    fields = (
        "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB",
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_NAME",
        "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH",
    )
    receipt = {name.lower(): os.environ.get(name, "") for name in fields}
    receipt["schema"] = 1
    receipt["checkout_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    checks = {
        "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
        "repository": receipt["github_repository"] == "morrowbyte/weasel-os",
        "runner": re.fullmatch(r"ci-weasel-os-\d+", receipt["runner_name"]) is not None,
        "environment": receipt["runner_environment"] == "self-hosted",
        "platform": receipt["runner_os"] == "Linux" and receipt["runner_arch"] == "X64",
        "source": receipt["checkout_sha"] == receipt["github_sha"],
    }
    receipt["checks"] = checks
    receipt["verified"] = all(checks.values())
    Path("boat-runner.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not receipt["verified"]:
        failures = ", ".join(name for name, passed in checks.items() if not passed)
        raise SystemExit(f"Boat-only routing refused: {failures}. See boat-runner.json.")
    summary = (
        f"Boat runner `{receipt['runner_name']}`; source `{receipt['checkout_sha']}`; "
        f"job `{receipt['github_job']}`. Independent VM mapping: Boat CI API.\n"
    )
    print(summary, end="")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
            output.write(summary)


if __name__ == "__main__":
    main()
