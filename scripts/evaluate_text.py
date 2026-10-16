"""Explicit live API evaluation; does not run as part of offline pytest."""

import json
from pathlib import Path
import re

from saywell.cleanup import ApiCleaner
from saywell.settings import Settings, read_key

root = Path(__file__).resolve().parents[1]
cases = json.loads((root / "tests/fixtures/cleanup-cases.json").read_text(encoding="utf-8"))
settings = Settings.load()
cleaner = ApiCleaner(
    read_key(api_base_url=settings.api_base_url),
    settings.model,
    settings.api_timeout,
    base_url=settings.api_base_url,
)
report = []
for case in cases:
    result = cleaner.clean(case["input"], settings.glossary)
    compact = re.sub(r"\s", "", result.text)
    missing = [
        phrase for phrase in case["required"] if not any(word in compact for word in phrase.split("|"))
    ]
    unexpected = [phrase for phrase in case["forbidden"] if phrase in result.text]
    success = not missing and not unexpected
    report.append(
        {
            "id": case["id"],
            "passed": success,
            "text": result.text,
            "warnings": result.warnings,
            "missing": missing,
            "unexpected": unexpected,
            "seconds": round(result.elapsed, 2),
        }
    )
    print(f"{case['id']}: {'PASS' if success else 'FAIL'} ({result.elapsed:.2f}s)", flush=True)
output = root / "private" / "text-evaluation.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
passed = sum(row["passed"] for row in report)
print(f"Passed {passed}/{len(report)}")
raise SystemExit(0 if passed == len(report) else 1)
