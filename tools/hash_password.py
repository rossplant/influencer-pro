"""Print a scrypt hash for the optional ADMIN_PASSWORD_HASH fallback.

The password is read from stdin so it is not part of the process arguments.

    python -m tools.hash_password
"""

from __future__ import annotations

import sys

import auth


def main() -> int:
    password = sys.stdin.readline()
    if password.endswith("\n"):
        password = password[:-1]
    if password == "":
        print("password is empty", file=sys.stderr)
        return 1
    print(auth.hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
