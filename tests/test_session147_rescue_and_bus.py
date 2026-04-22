"""SESSION-147 — Knowledge Bus Wiring + ComfyUI Interactive Path Rescue.

These tests pin the two "last-mile" fixes surfaced by the SESSION-146
blackbox audit:

1. ``mathart.workspace.knowledge_bus_factory.build_project_knowledge_bus``
   eagerly compiles the repository's ``knowledge/`` directory and is
   consumed by both the interactive wizard and the non-interactive
   ``DirectorStudioStrategy``.
2. ``mathart.workspace.comfyui_rescue`` replaces the previous hard
   ``comfyui_not_found`` exit with a friendly prompt that:
     * strips drag-and-drop quoting,
     * validates the target is a real ComfyUI root,
     * persists ``COMFYUI_HOME`` to ``.env`` (dotenv *or* native),
     * hot-injects it into ``os.environ`` and re-runs the radar.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mathart.workspace import (
    build_project_knowledge_bus,
    comfyui_rescue,
)
from mathart.workspace.comfyui_rescue import (
    COMFYUI_ENV_VAR,
    RescueOutcome,
    _clean_pasted_path,
    _looks_like_comfyui_root,
    hot_inject_env,
    is_comfyui_not_found_payload,
    persist_comfyui_home,
    prompt_comfyui_path_rescue,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Knowledge bus factory
# ---------------------------------------------------------------------------

class TestKnowledgeBusFactory:
    def test_factory_compiles_repository_knowledge(self):
        """The repo ships a ``knowledge/`` dir; the factory must compile it
        into a RuntimeDistillationBus with at least one module."""
        bus = build_project_knowledge_bus(
            project_root=PROJECT_ROOT,
            backend_preference=("python",),
        )
        assert bus is not None
        assert len(bus.compiled_spaces) >= 1
        total_constraints = sum(
            len(space.param_names) for space in bus.compiled_spaces.values()
        )
        assert total_constraints > 0

    def test_factory_returns_none_gracefully_on_missing_knowledge(self, tmp_path):
        """A workspace without ``knowledge/`` must *not* crash — the factory
        returns either None or an empty bus (both are acceptable "vacuum"
        states per the module's contract)."""
        bus = build_project_knowledge_bus(
            project_root=tmp_path,
            backend_preference=("python",),
        )
        # Either a None degradation or an empty bus is acceptable.
        if bus is not None:
            assert len(bus.compiled_spaces) == 0

    def test_director_intent_parser_accepts_factory_bus(self):
        """Smoke test: the factory's bus can be injected straight into
        DirectorIntentParser without further adaptation."""
        from mathart.workspace.director_intent import DirectorIntentParser

        bus = build_project_knowledge_bus(
            project_root=PROJECT_ROOT,
            backend_preference=("python",),
        )
        assert bus is not None
        parser = DirectorIntentParser(
            workspace_root=PROJECT_ROOT,
            knowledge_bus=bus,
        )
        assert parser.knowledge_bus is bus


# ---------------------------------------------------------------------------
# 2. Pasted-path cleaning
# ---------------------------------------------------------------------------

class TestCleanPastedPath:
    @pytest.mark.parametrize("raw,expected", [
        ('"D:\\ComfyUI"', "D:\\ComfyUI"),
        ("'/opt/ComfyUI'", "/opt/ComfyUI"),
        ("  /home/user/ComfyUI  \n", "/home/user/ComfyUI"),
        ("", ""),
        ('""', ""),
    ])
    def test_strips_quotes_and_whitespace(self, raw, expected):
        assert _clean_pasted_path(raw) == expected


# ---------------------------------------------------------------------------
# 3. ComfyUI root validation
# ---------------------------------------------------------------------------

class TestLooksLikeComfyUIRoot:
    def test_valid_root_passes(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("# fake comfy\n", encoding="utf-8")
        (tmp_path / "custom_nodes").mkdir()
        assert _looks_like_comfyui_root(tmp_path)

    def test_missing_main_py_fails(self, tmp_path: Path):
        (tmp_path / "custom_nodes").mkdir()
        assert not _looks_like_comfyui_root(tmp_path)

    def test_missing_custom_nodes_fails(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("# fake\n", encoding="utf-8")
        assert not _looks_like_comfyui_root(tmp_path)

    def test_file_instead_of_dir_fails(self, tmp_path: Path):
        f = tmp_path / "not_a_dir"
        f.write_text("x", encoding="utf-8")
        assert not _looks_like_comfyui_root(f)


# ---------------------------------------------------------------------------
# 4. Persistence & hot-injection
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_comfyui_env():
    """Autouse fixture that guarantees every test starts with NO
    ``COMFYUI_HOME`` in ``os.environ`` AND unconditionally rolls back
    any mutation at teardown.

    We bypass ``monkeypatch`` here because the production code path
    (``hot_inject_env``) mutates ``os.environ`` directly — monkeypatch
    only rewinds mutations that went *through* monkeypatch itself, so a
    raw ``os.environ[key] = value`` inside the SUT would leak.  A manual
    snapshot / restore cycle guarantees bit-for-bit isolation between
    tests in this file and every SESSION-146 radar test that assumes
    ``COMFYUI_HOME`` is unset.
    """
    sentinel = object()
    before = os.environ.get(COMFYUI_ENV_VAR, sentinel)
    os.environ.pop(COMFYUI_ENV_VAR, None)
    try:
        yield
    finally:
        if before is sentinel:
            os.environ.pop(COMFYUI_ENV_VAR, None)
        else:
            os.environ[COMFYUI_ENV_VAR] = before  # type: ignore[assignment]


def _bury_comfyui_under(tmp_path: Path) -> Path:
    """Return a fake ComfyUI root that is buried deep enough inside
    ``tmp_path`` to be OUT OF REACH of the radar's ``cwd.parent.parent``
    filesystem heuristic.

    SESSION-147 red line: tests must never leak a live ``ComfyUI``
    directory into the ``/tmp/pytest-of-*`` sibling tree, because the
    radar will happily find it via ``filesystem_heuristic`` and flip
    the ``test_manual_when_no_comfy`` baseline to ``auto_fixable``.
    """
    nested = tmp_path / "sandbox_root" / "engines" / "ComfyUI"
    nested.mkdir(parents=True)
    (nested / "main.py").write_text("# fake comfy\n", encoding="utf-8")
    (nested / "custom_nodes").mkdir()
    return nested


class TestPersistComfyUIHome:
    def test_creates_env_and_hot_injects(self, tmp_path: Path, monkeypatch):
        comfy = _bury_comfyui_under(tmp_path)

        env_path = persist_comfyui_home(tmp_path, comfy)
        assert env_path == tmp_path / ".env"
        assert env_path.exists()
        content = env_path.read_text(encoding="utf-8")
        assert "COMFYUI_HOME" in content
        # Value must contain the resolved path (or at least the leaf folder)
        assert "ComfyUI" in content

        # The autouse fixture above snapshots & restores os.environ, so
        # exercising the real ``hot_inject_env`` side-effect is safe.
        hot_inject_env(COMFYUI_ENV_VAR, str(comfy.resolve()))
        assert os.environ[COMFYUI_ENV_VAR] == str(comfy.resolve())

    def test_preserves_unrelated_keys(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "API_KEY=sk-existing\nMODEL=gpt-4.1-mini\n",
            encoding="utf-8",
        )
        comfy = _bury_comfyui_under(tmp_path)

        persist_comfyui_home(tmp_path, comfy)
        content = env_path.read_text(encoding="utf-8")
        assert "API_KEY=sk-existing" in content
        assert "MODEL=gpt-4.1-mini" in content
        assert "COMFYUI_HOME" in content

    def test_native_fallback_when_dotenv_missing(
        self, tmp_path: Path, monkeypatch
    ):
        """When python-dotenv is unimportable, the native appender still
        persists the key."""
        import builtins
        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "dotenv":
                raise ImportError("dotenv deliberately hidden for this test")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", guarded_import)

        comfy = _bury_comfyui_under(tmp_path)

        persist_comfyui_home(tmp_path, comfy)
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "COMFYUI_HOME" in content


# ---------------------------------------------------------------------------
# 5. Payload detection
# ---------------------------------------------------------------------------

class TestPayloadDetection:
    def test_detects_comfyui_not_found(self):
        payload = {
            "status": "blocked",
            "blocking_actions": [
                "comfyui_not_found: scan process table and ...",
                "python_env_critical: missing 4 core packages",
            ],
        }
        assert is_comfyui_not_found_payload(payload)

    def test_ignores_non_blocked_status(self):
        payload = {
            "status": "ready",
            "blocking_actions": ["comfyui_not_found: ..."],
        }
        assert not is_comfyui_not_found_payload(payload)

    def test_ignores_other_blockers(self):
        payload = {
            "status": "blocked",
            "blocking_actions": ["python_env_critical: ..."],
        }
        assert not is_comfyui_not_found_payload(payload)


# ---------------------------------------------------------------------------
# 6. Interactive rescue prompt
# ---------------------------------------------------------------------------

class TestInteractiveRescuePrompt:
    def _make_valid_comfy(self, tmp_path: Path) -> Path:
        return _bury_comfyui_under(tmp_path)

    def test_accepts_quoted_drag_and_drop_path(
        self, tmp_path: Path, monkeypatch
    ):
        comfy = self._make_valid_comfy(tmp_path)

        answers = iter([f'"{comfy}"'])
        messages: list[str] = []
        outcome = prompt_comfyui_path_rescue(
            project_root=tmp_path,
            input_fn=lambda _prompt: next(answers),
            output_fn=lambda m: messages.append(m),
        )

        assert isinstance(outcome, RescueOutcome)
        assert outcome.resolved is True
        assert outcome.path == str(comfy.resolve())
        assert outcome.env_file is not None
        # UX: must print the success marker
        assert any("引擎绑定成功" in m for m in messages)
        # Hot injection must be visible in os.environ (the autouse fixture
        # rolls it back after the test terminates, so sibling suites stay
        # unaffected by this real-side-effect exercise).
        assert os.environ[COMFYUI_ENV_VAR] == str(comfy.resolve())
        # Env file persisted
        env_text = Path(outcome.env_file).read_text(encoding="utf-8")
        assert "COMFYUI_HOME" in env_text

    def test_empty_input_falls_back_to_sandbox(self, tmp_path: Path):
        answers = iter([""])
        messages: list[str] = []
        outcome = prompt_comfyui_path_rescue(
            project_root=tmp_path,
            input_fn=lambda _prompt: next(answers),
            output_fn=lambda m: messages.append(m),
        )
        assert outcome.resolved is False
        assert outcome.fallback_to_sandbox is True
        assert outcome.path is None
        # Must NOT have created a .env for a refusal
        assert not (tmp_path / ".env").exists()

    def test_invalid_then_valid_path_eventually_succeeds(
        self, tmp_path: Path, monkeypatch
    ):
        """The user supplies a bogus path first, then a real one. The
        rescue must survive the first failure and accept the second."""
        comfy = self._make_valid_comfy(tmp_path)
        monkeypatch.delenv(COMFYUI_ENV_VAR, raising=False)
        bogus = tmp_path / "does_not_exist"
        answers = iter([str(bogus), str(comfy)])
        messages: list[str] = []

        outcome = prompt_comfyui_path_rescue(
            project_root=tmp_path,
            input_fn=lambda _prompt: next(answers),
            output_fn=lambda m: messages.append(m),
            max_attempts=3,
        )
        assert outcome.resolved is True
        assert any("路径无效" in m for m in messages)

    def test_max_attempts_then_fallback(self, tmp_path: Path):
        bogus = tmp_path / "nope"
        answers = iter([str(bogus), str(bogus), str(bogus)])
        messages: list[str] = []
        outcome = prompt_comfyui_path_rescue(
            project_root=tmp_path,
            input_fn=lambda _prompt: next(answers),
            output_fn=lambda m: messages.append(m),
            max_attempts=3,
        )
        assert outcome.resolved is False
        assert outcome.fallback_to_sandbox is True


# ---------------------------------------------------------------------------
# 7. ProductionStrategy integration
# ---------------------------------------------------------------------------

class TestProductionStrategyRescue:
    def test_accepts_input_output_hooks(self, tmp_path: Path):
        from mathart.workspace.mode_dispatcher import (
            ModeDispatcher,
            ProductionStrategy,
        )

        captured_outputs: list[str] = []

        def fake_input(_prompt: str) -> str:
            return ""  # instant fallback

        strategy = ProductionStrategy(
            tmp_path,
            input_fn=fake_input,
            output_fn=lambda m: captured_outputs.append(m),
        )
        assert strategy._input_fn is fake_input
        assert strategy._output_fn is not None

        dispatcher = ModeDispatcher(
            project_root=tmp_path,
            input_fn=fake_input,
            output_fn=lambda m: captured_outputs.append(m),
        )
        # The default ProductionStrategy registered by the dispatcher must
        # carry the injected hooks.
        prod = dispatcher._registry[strategy.mode]
        assert prod._input_fn is fake_input


# ---------------------------------------------------------------------------
# 8. Pyproject contract
# ---------------------------------------------------------------------------

class TestPyprojectContract:
    def test_python_dotenv_in_core_deps(self):
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert '"python-dotenv>=' in text
