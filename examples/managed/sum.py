"""A self-contained standard-library program for the local runner."""

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
total = sum(
    row["record"]["data"]["value"] for item in payload["inputs"].values() for row in item["records"]
)
Path(sys.argv[2]).write_text(json.dumps({"total": total}))
