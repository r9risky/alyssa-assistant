import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_actions_do_not_depend_on_brain_package():
    offenders = {}
    for path in (ROOT / "actions").glob("*.py"):
        imports = [name for name in _imports(path) if name == "brain" or name.startswith("brain.")]
        if imports:
            offenders[path.name] = imports
    assert offenders == {}


def test_dialogue_keeps_tool_catalog_separate():
    dialogue = (ROOT / "brain" / "dialogue.py").read_text(encoding="utf-8")
    catalog = (ROOT / "brain" / "tool_catalog.py").read_text(encoding="utf-8")
    registry = (ROOT / "brain" / "tool_registry.py").read_text(encoding="utf-8")
    assert "from .tool_registry import TOOLS, refresh_tools" in dialogue
    assert "BASE_TOOLS = [" in catalog
    assert "from .tool_catalog import BASE_TOOLS" in registry


def test_providers_do_not_import_dialogue_orchestration():
    offenders = {}
    for path in (ROOT / "brain" / "providers").glob("*.py"):
        imports = [
            name for name in _imports(path)
            if name == "brain.dialogue" or name.endswith(".dialogue")
        ]
        if imports:
            offenders[path.name] = imports
    assert offenders == {}


def test_actions_facade_does_not_eagerly_import_pyautogui():
    source = (ROOT / "actions" / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert "pyautogui" not in imports
