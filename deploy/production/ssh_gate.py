#!/usr/bin/python3
"""Forced SSH command: deployment operations, never an arbitrary shell."""
import os
import re
import sys


def arguments(command):
    parts = command.split()
    if parts in (["status"], ["rollback"]):
        return parts
    if len(parts) == 2 and parts[0] == "deploy" and re.fullmatch(r"[0-9a-f]{40}", parts[1]):
        return parts
    raise ValueError("Only deploy <40-character SHA>, status, or rollback is allowed")


def main():
    try:
        args = arguments(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    os.execv("/usr/bin/sudo", ["sudo", "-n", "/usr/local/sbin/farposton-release", *args])


if __name__ == "__main__":
    sys.exit(main())
