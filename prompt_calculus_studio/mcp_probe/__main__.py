"""Run the A01 synthetic PCS owner, MCP adapter or export the contract schemas."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for name in ("owner", "stdio"):
        sub = modes.add_parser(name)
        sub.add_argument("--profile", required=True, type=Path, help="Dedicated synthetic profile directory")
        sub.add_argument("--instance", required=True, type=UUID, help="Explicit paired instance UUID")
        if name == "owner":
            sub.add_argument("--minutes", type=int, default=15)
    modes.add_parser("schemas")
    args = parser.parse_args()
    if args.mode == "schemas":
        from .contracts import schemas
        print(json.dumps(schemas(), ensure_ascii=False, indent=2))
        return 0
    if args.mode == "owner":
        from .local import run_owner
        if not 1 <= args.minutes <= 60:
            parser.error("--minutes must be between 1 and 60")
        return run_owner(args.profile.absolute(), str(args.instance), args.minutes)
    from PySide6.QtCore import QCoreApplication
    from .bridge import serve
    app = QCoreApplication.instance() or QCoreApplication([])
    asyncio.run(serve(args.profile.absolute(), str(args.instance)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
