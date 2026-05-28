from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.finalize import finalize_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finalize official website results after manual review.")
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--review")
    parser.add_argument("--unresolved-output")
    args = parser.parse_args(argv)

    summary = finalize_results(
        args.results,
        args.output,
        review_csv=args.review,
        unresolved_csv=args.unresolved_output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
