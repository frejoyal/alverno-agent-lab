"""Structural checks of the agents image and of docker compose (docs/adr/0019).

Usage: python3 .github/scripts/check_docker.py <image>

Fails (exit 1) when the image runs as root or declares a port, when a file that
.dockerignore excludes is in the image, or when docker compose publishes a port, links
the agents to a network with a way out, or keeps Ollama's way out in the offline file.
Standard library only: it runs on the CI runner, outside the project's environment.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

# Paths under /app that must not exist in the image.
EXCLUDED = (
    "reports",
    "logs",
    ".cache",
    ".env",
    ".git",
    "tests",
    "docs",
    "data/eval/hr_holdout.jsonl",
    "data/eval/svc_holdout.jsonl",
    "data/eval/maint_holdout.jsonl",
    "data/eval/dir_holdout.jsonl",
)
PROBE = """
import glob, json, os
present = [p for p in {paths} if os.path.lexists(os.path.join('/app', p))]
present += glob.glob('/app/data/eval/*holdout*')
print(json.dumps(sorted(set(present))))
"""


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def image_problems(image: str) -> list[str]:
    config = json.loads(run("docker", "image", "inspect", image))[0]["Config"]
    problems = []
    user = (config.get("User") or "").split(":")[0]
    if user in ("", "root", "0"):
        problems.append(f"image user is root ({config.get('User')!r})")
    if config.get("ExposedPorts"):
        problems.append(f"image declares ports: {sorted(config['ExposedPorts'])}")
    probe = PROBE.format(paths=repr(EXCLUDED))
    found = json.loads(
        run(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            image,
            "-c",
            probe,
        )
    )
    if found:
        problems.append(f"excluded files in the image: {found}")
    return problems


def compose_problems(*files: str) -> tuple[list[str], dict[str, Any]]:
    args = ["docker", "compose"]
    for name in files:
        args += ["-f", name]
    config = json.loads(run(*args, "config", "--format", "json"))
    problems = []
    services = config["services"]
    networks = config.get("networks", {})
    for name, service in services.items():
        if service.get("ports"):
            problems.append(f"{' + '.join(files)}: service {name} publishes ports")
    agent_networks = set(services["alverno"].get("networks", {}))
    if agent_networks != {"interne"}:
        problems.append(f"{' + '.join(files)}: alverno networks {sorted(agent_networks)}")
    if not networks.get("interne", {}).get("internal"):
        problems.append(f"{' + '.join(files)}: network interne is not internal")
    return problems, config


def main() -> int:
    image = sys.argv[1]
    problems = image_problems(image)
    base, _ = compose_problems("compose.yaml")
    offline, config = compose_problems("compose.yaml", "compose.offline.yaml")
    problems += base + offline
    ollama_networks = set(config["services"]["ollama"].get("networks", {}))
    if ollama_networks != {"interne"}:
        problems.append(f"offline file: ollama networks {sorted(ollama_networks)}")
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("Docker structural checks: PASS")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
