"""Run the canonical 28 navigation probes on source, excluding local runtimes.

The historical verifier excludes venv by name. This checkout also has venv-ov,
whose installed SDK contains tens of thousands of generated files. Extend only
the in-process noise exclusions; do not alter queries, anchors or pass criteria.
"""
from pathlib import Path

from memory_agent.corpus import loader
from memory_agent.eval import eval_71_nav as navigation


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    environments = {path.name for path in root.iterdir()
                    if path.is_dir() and (path / "pyvenv.cfg").is_file()}
    # AOCI is explicitly unavailable; its generated cache is not source evidence.
    excluded = environments | {".aoci"}
    navigation._EXCLUDE_WALK_DIRS |= excluded
    loader.EXCLUDE_DIR_NAMES |= excluded
    print("Extra noise exclusions: " + ", ".join(sorted(excluded)), flush=True)
    raise SystemExit(navigation.main(["--refresh", "--verify", "--out",
                                      str(Path(__file__).with_name("navigation.json"))]))
