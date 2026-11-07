"""Export the common contract without importing application or loading .env."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.definitions import OPENAPI, provider_schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if exports differ from definitions"
    )
    args = parser.parse_args()
    outputs = {
        "openapi.json": OPENAPI,
        "provider-result.schema.json": provider_schema(),
    }
    for name, document in outputs.items():
        path = ROOT / "contracts" / name
        content = (
            json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != content:
                print(
                    f"Outdated contract: {path.relative_to(ROOT)}; run make contracts"
                )
                return 1
        else:
            path.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
