"""Target registry, loaded from targets.yml at the repo root.

A target is anything a probe points at. `kind` selects the probe implementation:
`api` targets get the httpx check, `journey` targets get a Playwright journey whose
name must match a test in probes/journeys/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Target:
    name: str
    kind: str  # "api" | "journey"
    url: str
    expect_status: int = 200
    expect_json_key: str | None = None
    journey: str | None = None
    extra: dict = field(default_factory=dict)


def load_targets(path: str | Path | None = None, kind: str | None = None,
                 name: str | None = None) -> list[Target]:
    raw = yaml.safe_load(Path(path or REPO_ROOT / "targets.yml").read_text())
    targets = []
    for entry in raw["targets"]:
        known = {k: v for k, v in entry.items() if k in Target.__dataclass_fields__}
        extra = {k: v for k, v in entry.items() if k not in Target.__dataclass_fields__}
        targets.append(Target(**known, extra=extra) if "extra" not in known else Target(**known))
    if kind:
        targets = [t for t in targets if t.kind == kind]
    if name:
        targets = [t for t in targets if t.name == name]
    return targets
