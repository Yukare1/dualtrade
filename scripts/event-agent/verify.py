"""Record separate event-agent acceptance after unit tests and CLI smoke passed."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import service


def main():
    cfg = service.load()
    source = service.source_hash()
    context = service.fingerprint({"config_hash": service.config_hash(cfg), "source_hash": source})
    core = service.read_json(service.ROOT / "build" / "acceptance.json", {})
    deployment = service.read_json(service.ROOT / "runtime" / "agent-deployment.json", {})
    parallel = service.read_json(service.ROOT / "runtime" / "reports" / "parallel-observation-deployment.json", {})
    events = service.STATE / "events.jsonl"
    smoke = False
    if events.exists():
        for line in events.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("event") == "smoke_passed":
                    smoke = True
            except json.JSONDecodeError:
                pass
    valid = (core.get("passed") is True and core.get("source_hash") == source and
             deployment.get("source_hash") == source and
             parallel.get("source_hash") == source and parallel.get("context") == context and
             smoke)
    if not valid:
        raise SystemExit("Current core acceptance, deployment and Codex smoke are required")
    receipt = {"passed": True, "checked_at": time.time(), "source_hash": source,
               "context": context, "service_fingerprint": service.service_fingerprint(),
               "validation": ["event-agent Python tests", "current native acceptance", "synthetic CLI REJECT"]}
    service.write_json(service.STATE / "acceptance.json", receipt)
    print(json.dumps({"passed": True, "service_fingerprint": receipt["service_fingerprint"]}))


if __name__ == "__main__":
    main()
