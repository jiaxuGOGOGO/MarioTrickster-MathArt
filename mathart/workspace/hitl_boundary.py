"""Unified contracts for human-in-the-loop boundary handoff.

This module centralizes the typed error and helper predicates used when
workspace automation reaches a physical or policy boundary that must be handed
back to the operator.
"""
from __future__ import annotations

import errno
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ManualOption:
    """One actionable operator choice exposed through the standard wizard UI."""

    key: str
    label: str
    description: str
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "recommended": self.recommended,
        }


class ManualInterventionRequiredError(RuntimeError):
    """Raised when bounded automation must stop and hand control to a human."""

    def __init__(
        self,
        *,
        code: str,
        title: str,
        message: str,
        options: Iterable[ManualOption],
        guidance: Iterable[str] = (),
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.title = title
        self.message = message
        self.options = tuple(options)
        self.guidance = tuple(str(item) for item in guidance)
        self.context = dict(context or {})
        super().__init__(f"{title}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "options": [item.to_dict() for item in self.options],
            "guidance": list(self.guidance),
            "context": self.context,
        }


class ManualChoiceAbortedError(RuntimeError):
    """Raised when the operator declines all manual recovery branches."""


def is_windows_symlink_privilege_error(exc: BaseException) -> bool:
    """Best-effort detection for Windows symlink privilege denials.

    The implementation intentionally accepts both true Windows ``winerror`` 1314
    surfaces and the Linux-hosted test doubles that use ``EPERM`` plus a
    privilege-related message.
    """

    winerror = getattr(exc, "winerror", None)
    if winerror == 1314:
        return True

    err_no = getattr(exc, "errno", None)
    text = f"{exc!r} {exc}".lower()
    if err_no == errno.EPERM and ("privilege" in text or "1314" in text or "symlink" in text):
        return True
    return False


def is_timeout_boundary(exc: BaseException | None) -> bool:
    """Return True when *exc* represents a timeout-like transport failure."""

    if exc is None:
        return False
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        text = f"{current!r} {current}".lower()
        if "timed out" in text or "timeout" in text:
            return True
        nested = [
            getattr(current, "reason", None),
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ]
        pending.extend(item for item in nested if isinstance(item, BaseException))
    return False


def rewrite_huggingface_url(url: str, endpoint: str) -> str:
    """Rewrite a Hugging Face URL to a mirror endpoint when possible."""

    normalized = endpoint.rstrip("/")
    if not url or "huggingface.co" not in url:
        return url
    return url.replace("https://huggingface.co", normalized, 1)


def symlink_manual_error(
    *,
    asset_name: str,
    source_path: str,
    target_path: str,
    size_bytes: int,
) -> ManualInterventionRequiredError:
    return ManualInterventionRequiredError(
        code="symlink_privilege_guard",
        title="需要人工确认：Windows 软链接权限不足",
        message=(
            f"检测到大文件资产 {asset_name} 在创建软链接时触发 WinError 1314 / 权限边界。"
            "为避免在未告知用户的情况下静默复制大文件，自动化已暂停。"
        ),
        options=(
            ManualOption(
                key="rerun_admin",
                label="以管理员身份重新运行",
                description="关闭当前流程，使用管理员权限重新启动，以便创建符号链接。",
                recommended=True,
            ),
            ManualOption(
                key="enable_dev_mode",
                label="启用 Windows 开发者模式 / 符号链接权限",
                description="按系统设置开启开发者模式或为当前用户授予 Create symbolic links 权限。",
            ),
            ManualOption(
                key="force_copy",
                label="确认执行全量复制",
                description="接受更慢且占用更多磁盘空间的完整复制，仅建议在你明确知情时选择。",
            ),
            ManualOption(
                key="abort",
                label="退出当前流程",
                description="保持现状，不执行任何磁盘写入。",
            ),
        ),
        guidance=(
            "微软官方将创建符号链接视为受权限控制的用户权限；默认仅授予管理员或受信任用户。",
            "当前实现已根据最小惊讶原则暂停，避免在后台偷偷复制数百 MB 乃至数 GB 文件。",
        ),
        context={
            "asset_name": asset_name,
            "source_path": source_path,
            "target_path": target_path,
            "size_bytes": size_bytes,
            "threshold_bytes": 500 * 1024 * 1024,
        },
    )


def network_timeout_manual_error(
    *,
    url: str,
    target_path: str,
    attempts: int,
    last_error: str,
) -> ManualInterventionRequiredError:
    return ManualInterventionRequiredError(
        code="network_timeout_guard",
        title="需要人工介入：网络下载连续超时",
        message=(
            f"下载目标 {target_path} 已连续超时 {attempts} 次。系统已熔断自动重试，"
            "避免进入无穷网络重试循环。"
        ),
        options=(
            ManualOption(
                key="configure_proxy",
                label="配置本地代理后重试",
                description="写入仅本地保存的代理配置，并将 HTTP(S)_PROXY 注入当前流程后重试。",
                recommended=True,
            ),
            ManualOption(
                key="use_hf_mirror",
                label="切换 Hugging Face 镜像后重试",
                description="将 Hugging Face 地址重写到镜像端点，并以受控方式重新尝试下载。",
            ),
            ManualOption(
                key="abort",
                label="退出当前流程",
                description="结束当前下载，不再继续网络访问。",
            ),
        ),
        guidance=(
            "下载器不会进行无限重试；达到阈值后必须把控制权交还给用户。",
            "如果你位于网络受限环境，优先选择本地代理或镜像源。",
        ),
        context={
            "url": url,
            "target_path": target_path,
            "attempts": attempts,
            "last_error": last_error,
        },
    )


__all__ = [
    "ManualChoiceAbortedError",
    "ManualInterventionRequiredError",
    "ManualOption",
    "is_timeout_boundary",
    "is_windows_symlink_privilege_error",
    "network_timeout_manual_error",
    "rewrite_huggingface_url",
    "symlink_manual_error",
]
