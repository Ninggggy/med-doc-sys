import re
import sys
from collections import OrderedDict
from pathlib import Path


PIP_HEADER = "  - pip:"
PIP_ITEM_PREFIX = "      - "


def requirement_key(requirement: str) -> str:
    match = re.match(r"^([A-Za-z0-9_.-]+)", requirement.strip())
    if not match:
        return requirement.strip().lower()
    return match.group(1).lower().replace("_", "-")


def extract_pip_requirements(env_text: str) -> list[str]:
    in_pip_block = False
    requirements: "OrderedDict[str, str]" = OrderedDict()

    for raw_line in env_text.splitlines():
        line = raw_line.rstrip()
        if not in_pip_block:
            if line == PIP_HEADER:
                in_pip_block = True
            continue

        if not line:
            continue

        if line.startswith(PIP_ITEM_PREFIX):
            requirement = line[len(PIP_ITEM_PREFIX) :].strip()
            if not requirement or requirement.startswith("#"):
                continue
            requirements[requirement_key(requirement)] = requirement
            continue

        if line.startswith("      #"):
            continue

        if not line.startswith("      "):
            break

    return list(requirements.values())


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python export_env_pip_requirements.py <env.yml> <requirements.txt>", file=sys.stderr)
        return 1

    env_path = Path(sys.argv[1])
    requirements_path = Path(sys.argv[2])
    requirements = extract_pip_requirements(env_path.read_text(encoding="utf-8"))
    requirements_path.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
