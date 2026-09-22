"""Architectural guard: the degree engine must stay free of the LLM.

The central claim of this project is that degree progress is *computed*, not
predicted. That claim is only credible if it is enforced mechanically. This test
fails the build if anyone imports the AI layer into the catalog or audit packages.

If this test ever fails, do not add an exception to it — move the code instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
PURE_PACKAGES = ["catalog", "audit"]
FORBIDDEN_ROOTS = {"llm", "advisor", "anthropic", "openai"}


def _python_files(package: str) -> list[Path]:
    return sorted((APP / package).rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("package", PURE_PACKAGES)
def test_engine_package_never_imports_the_ai_layer(package: str) -> None:
    files = _python_files(package)
    assert files, f"expected Python files in app/{package}/"

    violations: list[str] = []
    for path in files:
        for module in _imported_modules(path):
            parts = module.split(".")
            roots = {parts[0], parts[1] if len(parts) > 1 and parts[0] == "app" else ""}
            if roots & FORBIDDEN_ROOTS:
                violations.append(f"{path.relative_to(APP)} imports {module}")

    assert not violations, "degree engine must not depend on the AI layer:\n" + "\n".join(
        violations
    )


#: Inside app/advisor/, only chat.py may touch the model layer. facts.py computes
#: the grounding and answers.py writes the answer, and both must stay reachable
#: with no provider configured - that is what makes the demo work without a key.
DETERMINISTIC_ADVISOR_MODULES = ["facts.py", "answers.py"]


@pytest.mark.parametrize("module", DETERMINISTIC_ADVISOR_MODULES)
def test_the_deterministic_advisor_never_imports_the_model_layer(module: str) -> None:
    """The advisor's answer must not depend on a model being available.

    If this fails, an answer has started flowing through app/llm/ before it
    reaches the fallback path, and the no-API-key demo is broken - probably
    silently, because a configured machine would never notice.
    """
    path = APP / "advisor" / module
    assert path.exists(), f"expected app/advisor/{module}"

    offenders = sorted(
        m for m in _imported_modules(path) if m.split(".")[:2] in (["app", "llm"], ["llm"])
    )
    assert not offenders, (
        f"app/advisor/{module} must not import the model layer: {', '.join(offenders)}"
    )
