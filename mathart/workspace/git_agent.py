"""GitOps knowledge sync agent with strict whitelist discipline.

The agent is intentionally conservative: it refuses to package unknown paths,
validates staged knowledge carriers before commit, and degrades gracefully on
push errors so the main application never crashes.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_WHITELIST = (
    "knowledge",
    "PROJECT_BRAIN.json",
    "SESSION_HANDOFF.md",
    "tools/PROMPTS",
    "docs/research",
)


@dataclass(frozen=True)
class GitSyncResult:
    ok: bool
    staged_paths: tuple[str, ...]
    commit_message: str | None
    push_attempted: bool
    pushed: bool
    manual_action_required: bool
    reason: str


class GitAgent:
    """Synchronize declarative knowledge artifacts into Git using a whitelist."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        whitelist: Sequence[str] = DEFAULT_WHITELIST,
        remote_name: str = "origin",
        branch_name: str | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.whitelist = tuple(self._normalize_whitelist_entry(item) for item in whitelist)
        self.remote_name = remote_name
        self.branch_name = branch_name

    def preview_status(self) -> dict[str, object]:
        changed = self._git_changed_paths()
        allowed = [path for path in changed if self._is_allowed(path)]
        blocked = [path for path in changed if not self._is_allowed(path)]
        return {
            "changed_paths": changed,
            "allowed_paths": allowed,
            "blocked_paths": blocked,
            "whitelist": list(self.whitelist),
        }

    def sync_knowledge(
        self,
        *,
        paths: Sequence[str] | None = None,
        push: bool = True,
        commit_message: str | None = None,
        session_id: str = "SESSION-136",
    ) -> GitSyncResult:
        """Stage, validate, commit, and optionally push only whitelisted knowledge artifacts."""
        try:
            staged_candidates = self._resolve_stage_candidates(paths)
            if not staged_candidates:
                return GitSyncResult(
                    ok=False,
                    staged_paths=(),
                    commit_message=None,
                    push_attempted=False,
                    pushed=False,
                    manual_action_required=False,
                    reason="No knowledge changes eligible for GitOps sync.",
                )
            self._validate_paths(staged_candidates)
            self._git_add(staged_candidates)
            message = commit_message or self._build_commit_message(staged_candidates, session_id=session_id)
            commit_proc = self._git(
                ["commit", "-m", message],
                check=False,
            )
            if commit_proc.returncode != 0:
                combined = (commit_proc.stdout + "\n" + commit_proc.stderr).strip()
                if "nothing to commit" in combined.lower():
                    return GitSyncResult(
                        ok=True,
                        staged_paths=tuple(staged_candidates),
                        commit_message=message,
                        push_attempted=False,
                        pushed=False,
                        manual_action_required=False,
                        reason="Nothing new to commit after whitelist staging.",
                    )
                return GitSyncResult(
                    ok=False,
                    staged_paths=tuple(staged_candidates),
                    commit_message=message,
                    push_attempted=False,
                    pushed=False,
                    manual_action_required=True,
                    reason=f"git commit failed: {combined or 'unknown error'}",
                )
            if not push:
                return GitSyncResult(
                    ok=True,
                    staged_paths=tuple(staged_candidates),
                    commit_message=message,
                    push_attempted=False,
                    pushed=False,
                    manual_action_required=False,
                    reason="Committed locally; push skipped by caller.",
                )
            push_args = ["push", self.remote_name]
            if self.branch_name:
                push_args.append(self.branch_name)
            push_proc = self._git(push_args, check=False)
            if push_proc.returncode != 0:
                combined = (push_proc.stdout + "\n" + push_proc.stderr).strip()
                return GitSyncResult(
                    ok=True,
                    staged_paths=tuple(staged_candidates),
                    commit_message=message,
                    push_attempted=True,
                    pushed=False,
                    manual_action_required=True,
                    reason=f"git push degraded to manual action: {combined or 'unknown error'}",
                )
            return GitSyncResult(
                ok=True,
                staged_paths=tuple(staged_candidates),
                commit_message=message,
                push_attempted=True,
                pushed=True,
                manual_action_required=False,
                reason="Knowledge artifacts committed and pushed successfully.",
            )
        except Exception as exc:
            return GitSyncResult(
                ok=False,
                staged_paths=(),
                commit_message=commit_message,
                push_attempted=False,
                pushed=False,
                manual_action_required=True,
                reason=str(exc),
            )

    def _resolve_stage_candidates(self, paths: Sequence[str] | None) -> list[str]:
        if paths is not None:
            normalized = [self._normalize_repo_path(path) for path in paths]
            blocked = [path for path in normalized if not self._is_allowed(path)]
            if blocked:
                raise ValueError(
                    "Refusing to stage non-whitelisted paths: " + ", ".join(blocked)
                )
            return normalized

        changed = self._git_changed_paths()
        blocked = [path for path in changed if not self._is_allowed(path)]
        if blocked:
            raise ValueError(
                "Detected non-knowledge changes outside whitelist; refusing mixed GitOps package: "
                + ", ".join(blocked)
            )
        return [path for path in changed if self._is_allowed(path)]

    def _validate_paths(self, repo_paths: Iterable[str]) -> None:
        for repo_path in repo_paths:
            abs_path = self.project_root / repo_path
            if not abs_path.exists():
                raise FileNotFoundError(f"Whitelisted path does not exist: {repo_path}")
            if abs_path.is_dir():
                for child in sorted(abs_path.rglob("*")):
                    if child.is_file():
                        self._validate_file(child)
            else:
                self._validate_file(abs_path)

    def _validate_file(self, path: Path) -> None:
        suffix = path.suffix.lower()
        text_like = {".md", ".txt", ".json", ".yaml", ".yml"}
        if suffix not in text_like:
            return
        if suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
            return
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            raise ValueError(f"Knowledge artifact is empty: {path.relative_to(self.project_root)}")

    def _build_commit_message(self, repo_paths: Sequence[str], *, session_id: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        knowledge_count = sum(1 for item in repo_paths if item.startswith("knowledge/"))
        return (
            f"{session_id}: sync knowledge assets "
            f"(paths={len(repo_paths)}, knowledge={knowledge_count}, at={timestamp})"
        )

    def _git_add(self, repo_paths: Sequence[str]) -> None:
        self._git(["add", "--"] + list(repo_paths), check=True)

    def _git_changed_paths(self) -> list[str]:
        proc = self._git(["status", "--porcelain"], check=True)
        paths: list[str] = []
        for raw_line in proc.stdout.splitlines():
            if not raw_line.strip():
                continue
            path_blob = raw_line[3:]
            if " -> " in path_blob:
                path_blob = path_blob.split(" -> ", 1)[1]
            paths.append(self._normalize_repo_path(path_blob))
        return paths

    def _git(self, args: Sequence[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            check=check,
        )

    def _is_allowed(self, repo_path: str) -> bool:
        normalized = self._normalize_repo_path(repo_path)
        return any(
            normalized == entry or normalized.startswith(entry + "/")
            for entry in self.whitelist
        )

    @staticmethod
    def _normalize_whitelist_entry(path: str) -> str:
        return path.strip().strip("/")

    @staticmethod
    def _normalize_repo_path(path: str) -> str:
        normalized = path.replace("\\", "/").strip()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized.strip("/")


__all__ = ["DEFAULT_WHITELIST", "GitAgent", "GitSyncResult"]
