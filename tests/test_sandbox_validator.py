"""End-to-end toxin-trip assertions for the SESSION-138 sandbox validator.

These tests lock down the **four-dimensional anti-hallucination funnel** so
that a regression anywhere in the gate is caught immediately:

1. Provenance gate: a rule without ``source_quote`` is refused by the
   quarantine contract and never reaches ``active/``.
2. AST firewall: a malicious expression like ``__import__('os').system(...)``
   is rejected at parse time, not at eval time.
3. Math fuzz: ``1 / x`` with ``x = 0`` in the fuzz set raises
   :class:`MathToxinError`.
4. Physics dry-run: a rule with negative mass is refused outright.
5. Timeout: an intentionally slow gate call is caught by the 3 s watchdog.
6. GitOps red line: :func:`GitAgent.require_sandbox_report` refuses to
   trigger a push when any rule failed; ``PROTECTED_BRANCHES`` contains
   ``{"main", "master"}``.

The corresponding external research is summarised in
``docs/research/SESSION-138-KNOWLEDGE-QA-GATE-RESEARCH.md``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mathart.distill.parser import (
    KnowledgeParser,
    KnowledgeRule,
    QuarantineContractError,
    RuleType,
    TargetModule,
)
from mathart.distill.sandbox_validator import (
    FUZZ_SAMPLES,
    MathToxinError,
    PhysicsInstabilityError,
    SandboxTimeoutError,
    SandboxValidationError,
    SandboxValidator,
    UnsafeExpressionError,
    math_fuzz_expression,
    physics_dry_run,
    safe_parse_expression,
)
from mathart.workspace.git_agent import (
    GitAgent,
    PROPOSAL_BRANCH_PREFIX,
    PROTECTED_BRANCHES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_rule(
    *,
    rule_id: str = "unit_test_001",
    target_module: TargetModule = TargetModule.GENERAL,
    constraint: dict | None = None,
    source_quote: str = "Verbatim excerpt from a real book, at least 4 chars.",
    page_number: str | None = "p. 42",
) -> KnowledgeRule:
    return KnowledgeRule(
        id=rule_id,
        description="synthetic test rule",
        rule_type=RuleType.HEURISTIC,
        target_module=target_module,
        target_param="demo_param",
        constraint=constraint or {"type": "range", "min": 0.0, "max": 1.0},
        source="Synthetic Test Source",
        source_quote=source_quote,
        page_number=page_number,
        tags=["unit-test"],
    )


# ---------------------------------------------------------------------------
# Gate 1 — Provenance / source_quote contract
# ---------------------------------------------------------------------------


class TestProvenanceContract:
    def test_missing_source_quote_is_rejected_by_contract(self) -> None:
        rule = _make_rule(source_quote="")
        with pytest.raises(QuarantineContractError):
            rule.enforce_quarantine_contract()

    def test_too_short_source_quote_is_rejected(self) -> None:
        rule = _make_rule(source_quote="ok")
        with pytest.raises(QuarantineContractError):
            rule.enforce_quarantine_contract()

    def test_non_empty_source_quote_is_accepted(self) -> None:
        rule = _make_rule()
        rule.enforce_quarantine_contract()  # must not raise

    def test_validator_records_reason_for_missing_quote(self, tmp_path) -> None:
        validator = SandboxValidator(project_root=tmp_path)
        rule = _make_rule(source_quote="")
        report = validator.validate_rule(rule)
        assert report.passed is False
        assert any("provenance" in r for r in report.reasons)


# ---------------------------------------------------------------------------
# Gate 2 — AST-safe expression parsing
# ---------------------------------------------------------------------------


class TestAstFirewall:
    def test_pure_arithmetic_is_allowed(self) -> None:
        tree = safe_parse_expression("x * 2 + 1")
        assert tree is not None

    def test_whitelisted_math_call_is_allowed(self) -> None:
        tree = safe_parse_expression("sin(x) + cos(x)")
        assert tree is not None

    @pytest.mark.parametrize(
        "expr",
        [
            "__import__('os').system('rm -rf /')",
            "().__class__",
            "(1).__class__.__bases__",
            "os.system('whoami')",
            "eval('1+1')",
            "exec('print(1)')",
            "[i for i in range(10)]",
            "{1: 2}",
            "lambda y: y",
            "globals()",
            "open('/etc/passwd').read()",
        ],
    )
    def test_injection_payloads_are_rejected_before_eval(self, expr: str) -> None:
        with pytest.raises(UnsafeExpressionError):
            safe_parse_expression(expr)


# ---------------------------------------------------------------------------
# Gate 3 — Math fuzzing on canonical edge cases
# ---------------------------------------------------------------------------


class TestMathFuzz:
    def test_division_by_zero_in_fuzz_set_is_toxin(self) -> None:
        with pytest.raises(MathToxinError):
            math_fuzz_expression("1 / x")

    def test_log_negative_input_is_toxin(self) -> None:
        # math.log raises ValueError on negative inputs, which we tag as a
        # math toxin (see sandbox_validator._eval_safe).
        with pytest.raises(MathToxinError):
            math_fuzz_expression("log(x)")

    def test_safe_polynomial_passes_fuzz(self) -> None:
        reports = math_fuzz_expression("x * 0.5 + 1")
        assert len(reports) == len(FUZZ_SAMPLES)

    def test_overflow_on_extreme_input_is_toxin(self) -> None:
        # 1e6 ** 1e6 overflows well before reaching the other fuzz samples.
        with pytest.raises(MathToxinError):
            math_fuzz_expression("x ** 1e6")


# ---------------------------------------------------------------------------
# Gate 4 — Physics dry-run
# ---------------------------------------------------------------------------


class TestPhysicsDryRun:
    def test_reasonable_spring_is_stable(self) -> None:
        report = physics_dry_run(stiffness=10.0, damping=1.0, mass=1.0)
        assert report.passed is True
        assert report.penetrated is False

    def test_runaway_stiffness_is_flagged(self) -> None:
        report = physics_dry_run(stiffness=1e12, damping=0.0, mass=1.0)
        assert report.passed is False

    def test_validator_rejects_negative_mass(self, tmp_path) -> None:
        validator = SandboxValidator(project_root=tmp_path)
        rule = _make_rule(
            rule_id="physics_neg_mass",
            target_module=TargetModule.PHYSICS,
            constraint={"type": "exact", "value": -0.5},
        )
        rule.target_param = "mass"
        report = validator.validate_rule(rule)
        assert report.passed is False
        assert any("physics" in r for r in report.reasons)


# ---------------------------------------------------------------------------
# Gate 5 — 3-second hard watchdog
# ---------------------------------------------------------------------------


class TestSandboxTimeout:
    def test_timeout_raises_sandbox_timeout_error(self, tmp_path, monkeypatch) -> None:
        validator = SandboxValidator(project_root=tmp_path, timeout_seconds=0.1)

        # Monkeypatch the inner gate runner with a sleep so we deterministically
        # exceed the 0.1 s budget without depending on real wall clock in CI.
        import time

        def slow_gates(rule):
            time.sleep(1.0)
            return None, None

        monkeypatch.setattr(validator, "_run_all_gates", slow_gates)

        rule = _make_rule()
        report = validator.validate_rule(rule)
        assert report.passed is False
        assert any("timeout" in r for r in report.reasons)


# ---------------------------------------------------------------------------
# Cross-gate — Quarantine / Active dual-track discipline
# ---------------------------------------------------------------------------


class TestDualTrackDiscipline:
    def test_parser_never_surfaces_quarantine_when_recursive(self, tmp_path) -> None:
        knowledge = tmp_path / "knowledge"
        (knowledge / "quarantine").mkdir(parents=True)
        (knowledge / "active").mkdir(parents=True)

        # Drop a bad rule into quarantine and a good rule into active.
        bad = [
            {
                "id": "bad_1",
                "description": "should never reach mass production",
                "rule_type": "heuristic",
                "target_module": "general",
                "target_param": "x",
                "constraint": {"type": "exact", "value": 1},
                "source": "",
                "source_quote": "",
                "page_number": None,
                "tags": [],
            }
        ]
        good = [
            {
                "id": "good_1",
                "description": "safe rule",
                "rule_type": "heuristic",
                "target_module": "general",
                "target_param": "x",
                "constraint": {"type": "exact", "value": 0.5},
                "source": "Test book",
                "source_quote": "a verbatim quote",
                "page_number": "p. 1",
                "tags": [],
            }
        ]
        (knowledge / "quarantine" / "raw.json").write_text(json.dumps(bad))
        (knowledge / "active" / "promoted_rules.json").write_text(json.dumps(good))

        parser = KnowledgeParser()
        rules = parser.parse_directory(knowledge, recursive=True)
        rule_ids = {r.id for r in rules}
        assert "bad_1" not in rule_ids
        assert "good_1" in rule_ids

    def test_promote_rule_refuses_toxic_rule(self, tmp_path) -> None:
        (tmp_path / "knowledge").mkdir()
        validator = SandboxValidator(project_root=tmp_path)
        toxic = _make_rule(
            rule_id="toxic_1",
            constraint={"type": "formula", "expr": "1 / x"},
        )
        with pytest.raises(SandboxValidationError):
            validator.promote_rule(toxic)
        # Active directory must remain empty.
        active_files = list((tmp_path / "knowledge" / "active").glob("*.json"))
        assert active_files == []

    def test_promote_rule_accepts_clean_rule(self, tmp_path) -> None:
        (tmp_path / "knowledge").mkdir()
        validator = SandboxValidator(project_root=tmp_path)
        clean = _make_rule(
            rule_id="clean_1",
            constraint={"type": "formula", "expr": "x * 0.5 + 1"},
        )
        target = validator.promote_rule(clean)
        assert target.exists()
        payload = json.loads(target.read_text())
        assert payload[0]["id"] == "clean_1"


# ---------------------------------------------------------------------------
# Cross-gate — GitOps proposal-branch discipline
# ---------------------------------------------------------------------------


class TestGitOpsDiscipline:
    def test_protected_branch_constant_is_main_and_master(self) -> None:
        assert "main" in PROTECTED_BRANCHES
        assert "master" in PROTECTED_BRANCHES

    def test_proposal_branch_prefix_is_timestamped(self) -> None:
        assert PROPOSAL_BRANCH_PREFIX.startswith("knowledge-proposal/")

    def test_require_sandbox_report_blocks_on_failures(self) -> None:
        class _Report:
            passed = 3
            failed = 1

        with pytest.raises(ValueError):
            GitAgent.require_sandbox_report(_Report())

    def test_require_sandbox_report_allows_all_passed(self) -> None:
        class _Report:
            passed = 5
            failed = 0

        # Must not raise.
        GitAgent.require_sandbox_report(_Report())

    def test_require_sandbox_report_allows_none(self) -> None:
        GitAgent.require_sandbox_report(None)

    def test_agent_refuses_protected_branch_name(self, tmp_path) -> None:
        # Initialise an isolated repo so GitAgent._git calls do not mutate
        # the real project.
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.test"], cwd=tmp_path, check=True
        )
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        (tmp_path / "README.md").write_text("seed\n")
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True
        )

        agent = GitAgent(
            project_root=tmp_path,
            branch_name="main",
            use_proposal_branch=False,
        )
        result = agent.sync_knowledge(push=False)
        assert result.ok is False
        assert result.manual_action_required is True
        assert "protected" in (result.reason or "").lower() or "PROTECTED" in (result.reason or "")
