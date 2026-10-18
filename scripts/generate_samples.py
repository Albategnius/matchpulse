"""Write reproducible sample datasets (CSV + JSON with ground truth) to data/samples/.
Run: python scripts/generate_samples.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from matchpulse import SYNTHETIC_DISCLAIMER  # noqa: E402
from matchpulse.schema import events_to_csv  # noqa: E402
from matchpulse.synth import SCENARIOS, generate_match  # noqa: E402

SEED = 7


def main() -> None:
    out = ROOT / "data" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    for key in SCENARIOS:
        events, meta, gts = generate_match(key, SEED)
        stem = out / f"{key}_seed{SEED}"
        stem.with_suffix(".csv").write_text(f"# {SYNTHETIC_DISCLAIMER}\n" + events_to_csv(events))
        stem.with_suffix(".json").write_text(json.dumps(
            {"disclaimer": SYNTHETIC_DISCLAIMER, "meta": meta.model_dump(),
             "ground_truth": [g.to_dict() for g in gts], "events": [e.model_dump() for e in events]}, indent=1))
        print(f"{stem.name}: {len(events)} events, ground truth: {[g.pattern for g in gts]}")


if __name__ == "__main__":
    main()
