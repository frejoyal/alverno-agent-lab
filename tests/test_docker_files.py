"""Docker files of module 5c (docs/adr/0019), checked without Docker.

The CI job builds the image and runs the structural checks for real
(.github/scripts/check_docker.py); these tests pin the files themselves, so a change that
would open a port, run as root, ship a held-out set or give the agents a way out fails
before any build.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from .conftest import REPO_ROOT

DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
COMPOSE = REPO_ROOT / "compose.yaml"
OFFLINE = REPO_ROOT / "compose.offline.yaml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
# ADR 0019: never in the image.
REQUIRED_EXCLUSIONS = (
    "data/eval/*holdout*.jsonl",
    "reports/",
    "logs/",
    ".cache/",
    ".env",
    ".git/",
)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")


class _ComposeLoader(yaml.SafeLoader):
    """Safe loader that accepts the compose merge tags (!override, !reset)."""


def _tagged(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(_tag, _tagged)


def load(path: Path) -> dict[str, Any]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert isinstance(data, dict)
    return data


def dockerignore() -> list[str]:
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def instructions(name: str) -> list[str]:
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.split(" ", 1)[0].upper() == name]


# --------------------------------------------------------------------------- image
@pytest.mark.parametrize("pattern", REQUIRED_EXCLUSIONS)
def test_dockerignore_excludes(pattern: str) -> None:
    assert pattern in dockerignore()


@pytest.mark.parametrize(
    "name",
    ["hr_holdout.jsonl", "svc_holdout.jsonl", "maint_holdout.jsonl", "dir_holdout.jsonl"],
)
def test_every_held_out_set_matches_the_exclusion(name: str) -> None:
    assert (REPO_ROOT / "data" / "eval" / name).is_file()
    assert fnmatch.fnmatch(f"data/eval/{name}", "data/eval/*holdout*.jsonl")


def test_the_image_declares_no_port() -> None:
    assert instructions("EXPOSE") == []


def test_the_image_runs_as_a_fixed_non_root_user() -> None:
    users = instructions("USER")
    assert users, "no USER instruction: the image would run as root"
    user = users[-1].split(" ", 1)[1].split(":")[0]
    assert user not in ("root", "0") and user.isdigit()


def test_base_images_are_pinned_by_digest() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    base = re.search(r"^ARG PYTHON_IMAGE=(\S+)$", text, re.MULTILINE)
    assert base and DIGEST.search(base.group(1))
    for line in instructions("FROM"):
        image = line.split()[1]
        assert image == "${PYTHON_IMAGE}" or DIGEST.search(image), line


def test_the_build_installs_locked_runtime_dependencies_only() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv sync --locked --no-dev --no-editable" in text
    assert re.search(r"uv==\d+\.\d+\.\d+", text)
    assert "--extra" not in text and "--all-extras" not in text


# --------------------------------------------------------------------------- compose
def test_no_service_publishes_a_port() -> None:
    for name, service in load(COMPOSE)["services"].items():
        assert "ports" not in service, name


def test_the_agents_reach_only_the_internal_network() -> None:
    compose = load(COMPOSE)
    assert compose["services"]["alverno"]["networks"] == ["interne"]
    assert compose["networks"]["interne"]["internal"] is True


def test_the_agents_trust_the_ollama_host_without_the_remote_opt_in() -> None:
    env = load(COMPOSE)["services"]["alverno"]["environment"]
    assert env["ALVERNO_OLLAMA_URL"] == "http://ollama:11434"
    assert env["ALVERNO_TRUSTED_OLLAMA_HOSTS"] == '["ollama"]'
    # The remote opt-in would also open the Ollama cloud models, and Ollama has a way out.
    assert "ALVERNO_ALLOW_REMOTE_OLLAMA" not in env


def test_the_agents_container_is_read_only() -> None:
    service = load(COMPOSE)["services"]["alverno"]
    assert service["read_only"] is True
    assert "/tmp" in service["tmpfs"]


def test_ollama_is_pinned_and_leaves_the_context_to_the_clients() -> None:
    ollama = load(COMPOSE)["services"]["ollama"]
    assert ollama["image"].startswith("ollama/ollama:0.24.0@") and DIGEST.search(ollama["image"])
    # ADR 0005: the lab's clients set num_ctx on every call.
    assert "OLLAMA_CONTEXT_LENGTH" not in str(ollama.get("environment", ""))


def test_the_offline_file_removes_ollama_way_out() -> None:
    assert load(OFFLINE)["services"]["ollama"]["networks"] == ["interne"]
    assert "!override" in OFFLINE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- CI
def test_ci_builds_checks_and_never_pushes_the_image() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    job = yaml.safe_load(workflow)["jobs"]["docker"]
    steps = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker build" in steps
    assert "check_docker.py" in steps
    assert "--network none" in steps and "--read-only" in steps
    for command in ("docker push", "docker login", "docker/login-action", "build-push"):
        assert command not in workflow


@pytest.mark.parametrize(
    "gate",
    [
        "eval --mode retrieval --fake",
        "eval --mode full --fake",
        "privacy eval",
        "mcp eval --fake",
        "direction eval --mode tools --fake",
        "direction eval --mode full --fake",
    ],
)
def test_ci_runs_the_fake_gates_in_the_container(gate: str) -> None:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["docker"]
    steps = "\n".join(step.get("run", "") for step in job["steps"])
    assert f"gate {gate} " in steps
