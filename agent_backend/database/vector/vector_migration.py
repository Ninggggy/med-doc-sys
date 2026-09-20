from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.agent_backend.config.settings import settings
from agent.agent_backend.memory.storage.vector_store import VectorStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Export/import Milvus vector snapshots.")
    parser.add_argument("action", choices=["export", "import"], help="export or import snapshot")
    parser.add_argument("--collection", default=settings.vector_collection, help="Milvus collection name")
    parser.add_argument("--snapshot", default="", help="Snapshot file path")
    parser.add_argument("--replace", action="store_true", help="Replace collection when importing")
    args = parser.parse_args()

    store = VectorStore(args.collection)
    snapshot_path = args.snapshot.strip()
    if not snapshot_path:
        snapshot_path = str(Path(settings.vector_snapshot_dir) / f"{args.collection}.snapshot.json")

    if args.action == "export":
        result = store.export_snapshot(snapshot_path)
        print(json.dumps({"success": True, "action": "export", **result}, ensure_ascii=False, indent=2))
        return

    result = store.import_snapshot(snapshot_path, replace=bool(args.replace))
    print(json.dumps({"success": True, "action": "import", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
