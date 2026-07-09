from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: python -m gaius.task_worker <exit-path> <command...>", file=sys.stderr)
        return 2
    exit_path = Path(sys.argv[1])
    command = sys.argv[2:]
    try:
        completed = subprocess.run(command, check=False)
        code = completed.returncode
    except BaseException as exc:
        print(f"gaius task worker failed to start command: {exc}", file=sys.stderr)
        code = 127
    exit_path.write_text(str(code))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
