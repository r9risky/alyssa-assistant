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
    assert "from .tool_registry import refresh_tools, select_tools" in dialogue
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

def test_dialogue_orchestration_has_explicit_stages():
    tree = ast.parse((ROOT / "brain" / "dialogue.py").read_text(encoding="utf-8"))
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "_handle_pre_model_intent",
        "_build_model_request",
        "_normalize_tool_call",
        "_execute_tool_call",
        "_finalize_model_reply",
    } <= functions


def test_config_exposes_live_typed_sections_to_deep_modules():
    config_source = (ROOT / "config.py").read_text(encoding="utf-8")
    for section in (
        "PROVIDER_SETTINGS", "AUDIO_SETTINGS", "MEMORY_SETTINGS",
        "PLUGIN_SETTINGS", "UI_SETTINGS",
    ):
        assert section in config_source

    expected = {
        "memory.py": "from config import MEMORY_SETTINGS as config",
        "plugin_loader.py": "from config import PLUGIN_SETTINGS as config",
        "recorder.py": "from config import AUDIO_SETTINGS as config",
        "transcribe.py": "from config import AUDIO_SETTINGS as config",
        "voice_playback.py": "from config import AUDIO_SETTINGS as config",
        "voice_synthesis.py": "from config import AUDIO_SETTINGS as config",
        "brain/vision.py": "from config import PROVIDER_SETTINGS as config",
    }
    for relative, import_line in expected.items():
        assert import_line in (ROOT / relative).read_text(encoding="utf-8")


def test_topmost_window_behavior_has_one_shared_implementation():
    tree = ast.parse((ROOT / "overlay" / "widgets.py").read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for name in ("SpeechBubble", "ChatInputBar"):
        assert any(
            isinstance(base, ast.Name) and base.id == "_TopmostWindowMixin"
            for base in classes[name].bases
        )
    mixin_methods = {
        node.name for node in classes["_TopmostWindowMixin"].body
        if isinstance(node, ast.FunctionDef)
    }
    assert "showEvent" in mixin_methods
