#!/usr/bin/env python3
"""
Validate a TaxAnn annotation set against spec/annotation.schema.json.

  python tools/validate.py annotations/f1040_2025_slice.json

Exits 0 on success, 1 on the first schema violation (with the JSON path to it).
The schema IS the spec: if a file passes this, any conforming renderer can
consume it. Try changing a field's "type" to "banana" and re-running to see the
spec reject it.
"""

import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "spec" / "annotation.schema.json"


def main(argv):
    target = Path(argv[1]) if len(argv) > 1 else ROOT / "annotations" / "f1040_2025_slice.json"

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    doc = json.loads(target.read_text(encoding="utf-8"))

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))

    if not errors:
        print(f"VALID  {target.name}")
        print(f"       {len(doc.get('fields', []))} fields, "
              f"{len(doc.get('pages', []))} page(s), "
              f"spec={doc.get('spec')}")
        return 0

    print(f"INVALID  {target.name} — {len(errors)} error(s)\n")
    for err in errors:
        where = "$" + "".join(
            f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.path
        )
        print(f"  at {where}\n     {err.message}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
