from __future__ import annotations

import json

from mathart.brain.session_guard import SessionGuard


class TestSessionGuard:
    def test_register_task_persists_record(self, tmp_path):
        guard = SessionGuard(project_root=tmp_path)
        result = guard.register_task(
            session_id="SESSION-100",
            goal="Integrate character sprite pipeline into AssetPipeline",
            tags=["character", "pipeline"],
            files=["mathart/pipeline.py", "mathart/animation/character_renderer.py"],
            research_topics=["character sprites", "pixel animation"],
        )
        assert result.is_duplicate is False
        assert result.record_count == 1

        registry = tmp_path / "TASK_FINGERPRINTS.json"
        assert registry.exists()
        data = json.loads(registry.read_text(encoding="utf-8"))
        assert data["version"] == "1.0"
        assert len(data["records"]) == 1
        assert data["records"][0]["session_id"] == "SESSION-100"

    def test_exact_duplicate_detected(self, tmp_path):
        guard = SessionGuard(project_root=tmp_path)
        first = guard.register_task(
            session_id="SESSION-100",
            goal="Integrate character sprite pipeline into AssetPipeline",
            tags=["character", "pipeline"],
            files=["mathart/pipeline.py"],
            research_topics=["character sprites"],
        )
        second = guard.register_task(
            session_id="SESSION-101",
            goal="Integrate character sprite pipeline into AssetPipeline",
            tags=["character", "pipeline"],
            files=["mathart/pipeline.py"],
            research_topics=["character sprites"],
        )
        assert first.is_duplicate is False
        assert second.is_duplicate is True
        assert second.duplicate_candidates
        assert second.duplicate_candidates[0].session_id == "SESSION-100"
        assert second.duplicate_candidates[0].similarity == 1.0

    def test_similar_task_detected_by_overlap(self, tmp_path):
        guard = SessionGuard(project_root=tmp_path)
        guard.register_task(
            session_id="SESSION-200",
            goal="Add WFC tilemap generation to pipeline with playability checks",
            tags=["wfc", "level"],
            files=["mathart/pipeline.py", "mathart/level/wfc.py"],
            research_topics=["tilemap", "playability"],
        )
        matches = guard.find_similar(
            "Integrate WFC level pipeline and validate playability",
            tags=["wfc", "level"],
            files=["mathart/pipeline.py", "mathart/level/wfc.py"],
            research_topics=["tilemap", "playability"],
            similarity_threshold=0.4,
        )
        assert matches
        assert matches[0].session_id == "SESSION-200"
        assert matches[0].similarity >= 0.4

    def test_update_outcome(self, tmp_path):
        guard = SessionGuard(project_root=tmp_path)
        result = guard.register_task(
            session_id="SESSION-300",
            goal="Improve anti-duplication session startup flow",
        )
        ok = guard.update_outcome(
            session_id="SESSION-300",
            fingerprint=result.fingerprint,
            outcome="completed",
            notes="Added startup report and task fingerprint registry.",
        )
        assert ok is True

        guard2 = SessionGuard(project_root=tmp_path)
        assert guard2.records[0].outcome == "completed"
        assert "startup report" in guard2.records[0].notes

    def test_startup_report_reads_existing_project_files(self, tmp_path):
        (tmp_path / "DEDUP_REGISTRY.json").write_text(
            json.dumps(
                {
                    "absorbed_references": {"papers": [{"id": "P1"}], "tutorials": []},
                    "completed_changes": {"SESSION-001": [{"change": "X"}]},
                    "known_stagnation_patterns": {"patterns": [{"id": "S1"}, {"id": "S2"}]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (tmp_path / "PROJECT_BRAIN.json").write_text(
            json.dumps(
                {
                    "pending_tasks": [
                        {"id": "P1-NEW-5"},
                        {"id": "P1-NEW-1"},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        guard = SessionGuard(project_root=tmp_path)
        guard.register_task(
            session_id="SESSION-401",
            goal="Character sprite pipeline integration",
            tags=["character"],
        )
        report = guard.startup_report("Integrate character sprite pipeline")

        assert "SESSION_HANDOFF.md" in report["required_reads"]
        assert report["absorbed_reference_count"] == 1
        assert report["completed_change_count"] == 1
        assert report["known_stagnation_pattern_count"] == 2
        assert report["pending_task_count"] == 2
        assert report["duplicate_candidates"]

    def test_fingerprint_changes_when_scope_changes(self, tmp_path):
        guard = SessionGuard(project_root=tmp_path)
        fp1 = guard.build_fingerprint(
            "Integrate character sprite pipeline",
            files=["mathart/pipeline.py"],
        )
        fp2 = guard.build_fingerprint(
            "Integrate character sprite pipeline",
            files=["mathart/pipeline.py", "mathart/export/bridge.py"],
        )
        assert fp1 != fp2
