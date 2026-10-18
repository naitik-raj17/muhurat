"""Write the OpenAPI schema to a file: `python -m app.export_openapi openapi.json`.

The frontend generates its typed client from this file (`npm run gen:api`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    schema = create_app().openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {out} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
