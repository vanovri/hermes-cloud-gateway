from pathlib import Path
import os
import sys

home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
sys.path.insert(0, str(home / "ops"))
from startup_evidence import prepare, commit

if __name__ == "__main__":
    content = sys.stdin.read()
    if sys.argv[1] == "prepare":
        print(prepare(home, content))
    elif sys.argv[1] == "commit":
        commit(home, content)
        print("committed")
    else:
        raise SystemExit(2)
