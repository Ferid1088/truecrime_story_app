#!/usr/bin/env python3
"""Run one command in a new POSIX session for native local services."""
from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: detached_exec.py COMMAND [ARGS...]", file=sys.stderr)
        return 2
    if os.name == "posix":
        os.setsid()
    os.execvp(sys.argv[1], sys.argv[1:])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
