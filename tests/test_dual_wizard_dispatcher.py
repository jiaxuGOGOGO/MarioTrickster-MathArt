from __future__ import annotations

import builtins
import subprocess
from pathlib import Path

from mathart.workspace.config_manager import ConfigManager
from mathart.workspace.git_agent import GitAgent
from mathart.workspace.mode_dispatcher import ModeDispatcher, SessionMode


ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_local_distill_mode_preview_keeps_heavy_ai_imports_unloaded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("output/\n", encoding="utf-8")
    real_import = builtins.__import__
    attempted: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "torch" or name.startswith("mathart.comfy_client"):
            attempted.append(name)
            raise AssertionError(f"heavy module import should stay lazy: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    dispatcher = ModeDispatcher(project_root=tmp_path)
    result = dispatcher.dispatch("3", options={"interactive": False}, execute=False)

    assert result.session.mode is SessionMode.LOCAL_DISTILL
    assert result.executed is False
    assert attempted == []


def test_config_manager_writes_local_env_and_updates_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("output/\n", encoding="utf-8")
    answers = iter(["sk-local-test", "https://example.invalid/v1", "gpt-4.1-mini"])
    manager = ConfigManager(project_root=tmp_path)

    config = manager.ensure_local_api_config(
        interactive=True,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _message: None,
    )

    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")

    assert ".env" in gitignore_text
    assert "config.local.json" in gitignore_text
    assert "*.local.json" in gitignore_text
    assert "API_KEY=sk-local-test" in env_text
    assert config.model_name == "gpt-4.1-mini"
    assert config.source == "env_file"


def test_git_agent_refuses_mixed_non_knowledge_changes(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "ci@example.com")
    _git(tmp_path, "config", "user.name", "CI")
    (tmp_path / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "init")

    (tmp_path / "knowledge").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "rule.md").write_text("# rule\n\ncontent\n", encoding="utf-8")
    (tmp_path / "rogue.tmp").write_text("should never be gitops-packaged\n", encoding="utf-8")

    agent = GitAgent(project_root=tmp_path)
    result = agent.sync_knowledge(push=False, session_id="TEST-SESSION")

    assert result.ok is False
    assert result.manual_action_required is True
    assert "non-knowledge changes" in result.reason.lower() or "refusing mixed" in result.reason.lower()


def test_manus_cloud_distill_prompt_asset_is_present() -> None:
    prompt_path = ROOT / "tools" / "PROMPTS" / "manus_cloud_distill.md"
    assert prompt_path.exists()
    content = prompt_path.read_text(encoding="utf-8")
    assert "knowledge/" in content
    assert "PROJECT_BRAIN.json" in content
    assert "SESSION_HANDOFF.md" in content
    assert "git add ." in content
