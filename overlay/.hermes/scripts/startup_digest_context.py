from pathlib import Path
import os
import sys

home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
sys.path.insert(0, str(home / "ops"))
from startup_evidence import compact_context

print(compact_context(home))
