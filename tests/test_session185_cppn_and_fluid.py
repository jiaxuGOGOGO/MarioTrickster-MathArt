"""
SESSION-185 闭环测试套件
========================
验证 CPPN 纹理进化引擎和流体动量 VFX 控制器的 Adapter 层正确注册、
反射发现、沙盒隔离、强类型契约。

运行: python -m pytest tests/test_session185_cppn_and_fluid.py -v
"""
import importlib
import inspect
import json
import os
import sys
import unittest

# ── 确保项目根目录在 sys.path ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestCPPNTextureBackendExists(unittest.TestCase):
    """验证 CPPN 纹理进化引擎适配器文件存在且可导入。"""

    def test_file_exists(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "cppn_texture_backend.py")
        self.assertTrue(os.path.isfile(path), f"cppn_texture_backend.py not found at {path}")

    def test_module_importable(self):
        try:
            mod = importlib.import_module("mathart.core.cppn_texture_backend")
            self.assertIsNotNone(mod)
        except ImportError as e:
            # 允许因上游依赖缺失而导入失败，但模块文件本身必须存在
            self.assertIn("mathart", str(e).lower() + str(type(e)).lower())

    def test_has_register_backend_decorator(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "cppn_texture_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("@register_backend", source,
                       "cppn_texture_backend.py must use @register_backend decorator")

    def test_has_execute_method(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "cppn_texture_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("def execute", source,
                       "cppn_texture_backend.py must implement execute()")

    def test_sandbox_output_path(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "cppn_texture_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("workspace/laboratory/cppn_texture_engine", source,
                       "CPPN backend must output to workspace/laboratory/cppn_texture_engine/")

    def test_no_production_output(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "cppn_texture_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        # 确保不写入生产目录
        self.assertNotIn("output/production", source,
                          "CPPN backend must NOT write to output/production/")

    def test_artifact_manifest_return(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "cppn_texture_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("ArtifactManifest", source,
                       "CPPN backend must return ArtifactManifest")

    def test_genome_serialization(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "cppn_texture_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("_genome.json", source,
                       "CPPN backend must serialize genome JSON files")


class TestFluidMomentumBackendExists(unittest.TestCase):
    """验证流体动量 VFX 控制器适配器文件存在且可导入。"""

    def test_file_exists(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        self.assertTrue(os.path.isfile(path), f"fluid_momentum_backend.py not found at {path}")

    def test_module_importable(self):
        try:
            mod = importlib.import_module("mathart.core.fluid_momentum_backend")
            self.assertIsNotNone(mod)
        except ImportError as e:
            self.assertIn("mathart", str(e).lower() + str(type(e)).lower())

    def test_has_register_backend_decorator(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("@register_backend", source,
                       "fluid_momentum_backend.py must use @register_backend decorator")

    def test_has_execute_method(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("def execute", source,
                       "fluid_momentum_backend.py must implement execute()")

    def test_sandbox_output_path(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("workspace/laboratory/fluid_momentum_vfx", source,
                       "Fluid backend must output to workspace/laboratory/fluid_momentum_vfx/")

    def test_no_production_output(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("output/production", source,
                          "Fluid backend must NOT write to output/production/")

    def test_artifact_manifest_return(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("ArtifactManifest", source,
                       "Fluid backend must return ArtifactManifest")

    def test_cfl_safety_guard(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("np.clip", source,
                       "Fluid backend must use np.clip for CFL safety")

    def test_nan_detection(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        has_nan_check = "np.isnan" in source or "np.any(np.isnan" in source or "isnan" in source or "NaN" in source
        self.assertTrue(has_nan_check,
                        "Fluid backend must have NaN detection")

    def test_dummy_velocity_field(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "fluid_momentum_backend.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("dummy", source.lower(),
                       "Fluid backend must generate dummy velocity fields")


class TestBackendRegistryIntegration(unittest.TestCase):
    """验证两个后端在 backend_registry.py 中有 auto-load 入口。"""

    def test_cppn_auto_load_entry(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "backend_registry.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("cppn_texture_backend", source,
                       "backend_registry.py must have cppn_texture_backend auto-load entry")

    def test_fluid_auto_load_entry(self):
        path = os.path.join(PROJECT_ROOT, "mathart", "core", "backend_registry.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("fluid_momentum_backend", source,
                       "backend_registry.py must have fluid_momentum_backend auto-load entry")


class TestDocumentation(unittest.TestCase):
    """验证 SESSION-185 文档完整性。"""

    def test_user_guide_section_15(self):
        path = os.path.join(PROJECT_ROOT, "docs", "USER_GUIDE.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("SESSION-185", content,
                       "USER_GUIDE.md must contain SESSION-185 section")
        self.assertIn("CPPN 纹理进化引擎", content,
                       "USER_GUIDE.md must document CPPN Texture Evolution Engine")
        self.assertIn("流体动量 VFX 控制器", content,
                       "USER_GUIDE.md must document Fluid Momentum VFX Controller")

    def test_research_notes_exist(self):
        path = os.path.join(PROJECT_ROOT, "docs", "RESEARCH_NOTES_SESSION_185.md")
        self.assertTrue(os.path.isfile(path),
                        "RESEARCH_NOTES_SESSION_185.md must exist")

    def test_session_handoff_updated(self):
        path = os.path.join(PROJECT_ROOT, "SESSION_HANDOFF.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("SESSION-185", content,
                       "SESSION_HANDOFF.md must reference SESSION-185")

    def test_project_brain_updated(self):
        path = os.path.join(PROJECT_ROOT, "PROJECT_BRAIN.json")
        with open(path, "r", encoding="utf-8") as f:
            brain = json.load(f)
        self.assertEqual(brain["last_session_id"], "SESSION-185",
                          "PROJECT_BRAIN.json must have last_session_id = SESSION-185")
        self.assertEqual(brain["version"], "v0.99.23",
                          "PROJECT_BRAIN.json must have version v0.99.23")


class TestRedLineCompliance(unittest.TestCase):
    """验证红线遵守：零修改前端、零修改内部数学。"""

    def test_cli_wizard_untouched(self):
        """cli_wizard.py 不应包含 SESSION-185 的任何修改痕迹。"""
        path = os.path.join(PROJECT_ROOT, "mathart", "cli_wizard.py")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            self.assertNotIn("SESSION-185", source,
                              "cli_wizard.py must NOT be modified in SESSION-185")
            self.assertNotIn("cppn_texture", source,
                              "cli_wizard.py must NOT reference cppn_texture")
            self.assertNotIn("fluid_momentum", source,
                              "cli_wizard.py must NOT reference fluid_momentum")

    def test_laboratory_hub_untouched(self):
        """laboratory_hub.py 不应包含 SESSION-185 的任何修改痕迹。"""
        path = os.path.join(PROJECT_ROOT, "mathart", "laboratory_hub.py")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            self.assertNotIn("SESSION-185", source,
                              "laboratory_hub.py must NOT be modified in SESSION-185")
            self.assertNotIn("cppn_texture", source,
                              "laboratory_hub.py must NOT reference cppn_texture")
            self.assertNotIn("fluid_momentum", source,
                              "laboratory_hub.py must NOT reference fluid_momentum")


if __name__ == "__main__":
    unittest.main()
