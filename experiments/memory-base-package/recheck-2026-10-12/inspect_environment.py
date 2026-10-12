"""Inspect an installed environment without reading service data or secrets."""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import re
import sys
import sysconfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean-target", action="store_true")
    parser.add_argument("--forbidden-site", type=Path)
    args = parser.parse_args()
    site = Path(sysconfig.get_path("purelib")).resolve()
    from memory_base.deploy import PINS
    import memory_base
    package = Path(memory_base.__file__).resolve()
    distribution = metadata.distribution("agent-memory-base")
    assert package.is_relative_to(site), "Product import escaped the installed environment"
    assert Path(distribution._path).resolve().is_relative_to(site), "Product distribution escaped environment"
    distributions = {name: {"version": metadata.version(name),
                           "root": str(metadata.distribution(name)._path)} for name in PINS}
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    all_distributions = {normalize(dist.metadata["Name"]): {"version": dist.version, "root": str(dist._path)}
                         for dist in metadata.distributions()}
    constraints = {}
    for line in package.with_name("constraints.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
        if match:
            constraints[normalize(match[1])] = match[2]
    active_constraints = {name: version for name, version in constraints.items() if name in all_distributions}
    modules = {name: importlib.util.find_spec(name).origin for name in
               ("memory_base", "fastapi", "pydantic", "mcp", "openviking", "llama_cpp", "openai")}
    assert {name: item["version"] for name, item in distributions.items()} == PINS
    pths = [{"path": str(path), "content": path.read_text(encoding="utf-8")}
            for path in site.glob("*.pth")]
    if args.clean_target:
        assert "include-system-site-packages = false" in (Path(sys.prefix) / "pyvenv.cfg").read_text().lower()
        assert str(args.forbidden_site.resolve()) not in [str(Path(p).resolve()) for p in sys.path if p]
        assert all(Path(item["root"]).resolve().is_relative_to(site) for item in distributions.values())
        assert all(Path(item["root"]).resolve().is_relative_to(site) for item in all_distributions.values())
        assert all(all_distributions[name]["version"] == version for name, version in active_constraints.items())
        assert all(Path(origin).resolve().is_relative_to(site) for origin in modules.values())
        assert all(str(args.forbidden_site.resolve()) not in item["content"] for item in pths)
    print(json.dumps({"python": sys.version, "executable": sys.executable, "prefix": sys.prefix,
                      "base_prefix": sys.base_prefix, "site": str(site), "sys_path": sys.path,
                      "product_file": str(package), "product_distribution": str(distribution._path),
                      "direct_url": json.loads(distribution.read_text("direct_url.json")),
                      "pins": PINS, "distributions": distributions, "modules": modules, "pths": pths,
                      "all_distributions": all_distributions, "active_constraints": active_constraints,
                      "clean_target": args.clean_target}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
