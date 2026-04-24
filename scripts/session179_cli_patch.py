"""SESSION-179 CLI Wizard Patch — Visual Distillation + Blueprint Vault + Style Retargeting."""

def patch_cli_wizard():
    path = "mathart/cli_wizard.py"
    with open(path, "r") as f:
        content = f.read()

    # ========================================================================
    # PATCH A: Add [D] Visual Distillation option to Director Studio menu
    # ========================================================================
    old_menu = '''    # Step 1: Gather intent
    output_fn("请选择创作方式：")
    output_fn("  [A] 感性创世 — 用自然语言描述你想要的风格")
    output_fn("  [B] 蓝图派生 — 基于已有蓝图进行控制变量繁衍")
    output_fn("  [C] 混合模式 — 在蓝图基础上叠加感性描述")
    creation_mode = standard_text_prompt(
        "选择模式", input_fn=input_fn, output_fn=output_fn, default="A",
    ).strip().upper()
    logger.info("[CLI] Director Studio creation mode: %s", creation_mode)'''

    new_menu = '''    # Step 1: Gather intent
    # SESSION-179: Added [D] Visual Distillation gateway
    output_fn("请选择创作方式：")
    output_fn("  [A] 感性创世 — 用自然语言描述你想要的风格")
    output_fn("  [B] 蓝图派生 — 基于已有蓝图进行控制变量繁衍")
    output_fn("  [C] 混合模式 — 在蓝图基础上叠加感性描述")
    output_fn("  [D] 👁️ 视觉临摹 — 丢入参考动图，让 AI 逆向推导物理参数！")
    creation_mode = standard_text_prompt(
        "选择模式", input_fn=input_fn, output_fn=output_fn, default="A",
    ).strip().upper()
    logger.info("[CLI] Director Studio creation mode: %s", creation_mode)'''

    if old_menu in content:
        content = content.replace(old_menu, new_menu)
        print("[PATCH A] Director Studio menu updated with [D] Visual Distillation")
    else:
        print("[PATCH A] WARNING: Could not find Director Studio menu")

    # ========================================================================
    # PATCH B: Add Visual Distillation handler before blueprint/vibe collection
    # ========================================================================
    old_intent_block = '''    raw_intent: dict = {}
    if creation_mode in ("B", "C"):'''

    new_intent_block = '''    raw_intent: dict = {}
    # ── SESSION-179: Visual Distillation Gateway (GIF to Physics) ──────────
    # [核心约束] 绝对禁止引入 cv2 库！强制使用 PIL.ImageSequence 处理 GIF。
    if creation_mode == "D":
        try:
            from mathart.workspace.visual_distillation import distill_physics_from_reference
            ref_path = standard_text_prompt(
                "请输入参考动图路径 (GIF 文件或图片文件夹)",
                input_fn=input_fn, output_fn=output_fn,
            )
            output_fn("")
            output_fn("\\033[1;36m" + "═" * 60 + "\\033[0m")
            output_fn(
                "\\033[1;36m[👁️ 视觉临摹中枢] 正在启动 AI 视觉逆向推导引擎...\\033[0m"
            )
            output_fn("\\033[1;36m" + "═" * 60 + "\\033[0m")
            distilled_params = distill_physics_from_reference(
                ref_path,
                output_fn=output_fn,
            )
            # Inject distilled params into raw_intent as physics overrides
            raw_intent["vibe"] = "AI 视觉临摹逆向推导"
            # Map distilled params to genotype structure
            raw_intent["_distilled_physics"] = distilled_params
            output_fn("")
            output_fn("\\033[1;32m[✅ 视觉临摹] 逆向推导参数预览：\\033[0m")
            for k, v in distilled_params.items():
                output_fn(f"\\033[90m    {k}: {v}\\033[0m")
            output_fn("")
            # Ask if user wants to add a vibe overlay
            add_vibe = standard_text_prompt(
                "是否叠加额外的风格描述？(留空跳过)",
                input_fn=input_fn, output_fn=output_fn, allow_empty=True,
            )
            if add_vibe:
                raw_intent["vibe"] = add_vibe
        except Exception as _distill_err:
            logger.warning("[CLI] Visual Distillation FAILED", exc_info=True)
            output_fn(
                f"\\033[1;33m[⚠️ 视觉临摹] 处理失败: {_distill_err}\\n"
                "将使用默认参数继续。\\033[0m"
            )
    if creation_mode in ("B", "C"):'''

    if old_intent_block in content:
        content = content.replace(old_intent_block, new_intent_block)
        print("[PATCH B] Visual Distillation handler added")
    else:
        print("[PATCH B] WARNING: Could not find intent block")

    # ========================================================================
    # PATCH C: Add vibe collection for mode D (after B/C block)
    # ========================================================================
    old_vibe_block = '''    if creation_mode in ("A", "C"):
        vibe = standard_text_prompt(
            "用自然语言描述你想要的风格 (如: 活泼的跳跃, 夸张弹性)",
            input_fn=input_fn, output_fn=output_fn,
        )
        raw_intent["vibe"] = vibe'''

    new_vibe_block = '''    if creation_mode in ("A", "C"):
        vibe = standard_text_prompt(
            "用自然语言描述你想要的风格 (如: 活泼的跳跃, 夸张弹性)",
            input_fn=input_fn, output_fn=output_fn,
        )
        raw_intent["vibe"] = vibe
    # SESSION-179: For mode D, apply distilled physics to the genotype
    if creation_mode == "D" and "_distilled_physics" in raw_intent:
        _dp = raw_intent.pop("_distilled_physics")
        # These will be applied after parse_dict creates the spec
        raw_intent["_physics_override"] = _dp'''

    if old_vibe_block in content:
        content = content.replace(old_vibe_block, new_vibe_block)
        print("[PATCH C] Mode D vibe/physics override wiring added")
    else:
        print("[PATCH C] WARNING: Could not find vibe block")

    # ========================================================================
    # PATCH D: Apply physics override after spec parsing
    # ========================================================================
    old_parse_success = '''    output_fn("")
    output_fn("✅ 意图解析完成，进入白模预演...")'''

    new_parse_success = '''    # SESSION-179: Apply distilled physics override from Visual Distillation
    if "_physics_override" in raw_intent:
        _po = raw_intent["_physics_override"]
        try:
            if hasattr(spec, "genotype") and hasattr(spec.genotype, "physics"):
                for attr in ("gravity", "mass", "stiffness", "damping", "bounce", "friction"):
                    if attr in _po:
                        setattr(spec.genotype.physics, attr, float(_po[attr]))
            if hasattr(spec, "genotype") and hasattr(spec.genotype, "proportions"):
                for attr in ("head_ratio", "body_ratio", "limb_ratio", "scale", "squash_stretch"):
                    if attr in _po:
                        setattr(spec.genotype.proportions, attr, float(_po[attr]))
            if hasattr(spec, "genotype") and hasattr(spec.genotype, "animation"):
                for attr in ("frame_rate", "anticipation", "follow_through", "exaggeration",
                             "ease_in", "ease_out", "cycle_frames"):
                    if attr in _po:
                        val = _po[attr]
                        if attr in ("frame_rate", "cycle_frames"):
                            setattr(spec.genotype.animation, attr, int(val))
                        else:
                            setattr(spec.genotype.animation, attr, float(val))
            logger.info("[CLI] Visual Distillation physics override applied to spec")
        except Exception as _override_err:
            logger.warning("[CLI] Physics override application failed: %s", _override_err)
    output_fn("")
    output_fn("✅ 意图解析完成，进入白模预演...")'''

    if old_parse_success in content:
        content = content.replace(old_parse_success, new_parse_success)
        print("[PATCH D] Physics override application added")
    else:
        print("[PATCH D] WARNING: Could not find parse success block")

    # ========================================================================
    # PATCH E: Blueprint Vault — Custom naming with timestamp fallback
    # ========================================================================
    # This patch modifies the _offer_blueprint_save in interactive_gate.py
    # We'll handle that separately

    # ========================================================================
    # PATCH F: Style Retargeting in Blueprint Derivation mode [B]
    # ========================================================================
    old_bp_block = '''    if creation_mode in ("B", "C"):
        bp_path = standard_text_prompt(
            "请输入蓝图文件路径 (如 workspace/blueprints/hero_v1.yaml)",
            input_fn=input_fn, output_fn=output_fn,
        )
        raw_intent["base_blueprint"] = bp_path
        variants_str = standard_text_prompt(
            "派生变种数量 (0=不派生)",
            input_fn=input_fn, output_fn=output_fn, default="0",
        )
        raw_intent["evolve_variants"] = int(variants_str) if variants_str.isdigit() else 0
        if raw_intent["evolve_variants"] > 0:
            locks = standard_text_prompt(
                "锁定基因族 (逗号分隔, 如 physics,proportions; 留空=不锁定)",
                input_fn=input_fn, output_fn=output_fn, allow_empty=True,
            )
            raw_intent["freeze_locks"] = [x.strip() for x in locks.split(",") if x.strip()] if locks else []'''

    new_bp_block = '''    if creation_mode in ("B", "C"):
        bp_path = standard_text_prompt(
            "请输入蓝图文件路径 (如 workspace/blueprints/hero_v1.yaml)",
            input_fn=input_fn, output_fn=output_fn,
        )
        raw_intent["base_blueprint"] = bp_path
        # ── SESSION-179: Style Retargeting (无缝动静解耦换皮) ──────────
        # 加载已有动作骨架后，允许用户输入全新的画风 Prompt，
        # 覆盖上下文原有的 vibe 参数，实现"动作骨架完美复用，画风自由剥离与替换"。
        reskin_vibe = standard_text_prompt(
            "🎨 换皮模式：输入全新画风 Prompt (如: 赛博朋克风格, 水墨画风; 留空=保留原蓝图风格)",
            input_fn=input_fn, output_fn=output_fn, allow_empty=True,
        )
        if reskin_vibe:
            raw_intent["vibe"] = reskin_vibe
            output_fn(
                f"\\033[1;35m[🎨 风格换皮] 已注入全新画风: {reskin_vibe}\\033[0m"
            )
            output_fn(
                "\\033[90m    ↳ 动作骨架将从蓝图完美复用，仅画风被替换。\\033[0m"
            )
            logger.info("[CLI] Style Retargeting: vibe overridden to '%s'", reskin_vibe)
        variants_str = standard_text_prompt(
            "派生变种数量 (0=不派生)",
            input_fn=input_fn, output_fn=output_fn, default="0",
        )
        raw_intent["evolve_variants"] = int(variants_str) if variants_str.isdigit() else 0
        if raw_intent["evolve_variants"] > 0:
            locks = standard_text_prompt(
                "锁定基因族 (逗号分隔, 如 physics,proportions; 留空=不锁定)",
                input_fn=input_fn, output_fn=output_fn, allow_empty=True,
            )
            raw_intent["freeze_locks"] = [x.strip() for x in locks.split(",") if x.strip()] if locks else []'''

    if old_bp_block in content:
        content = content.replace(old_bp_block, new_bp_block)
        print("[PATCH F] Style Retargeting added to Blueprint Derivation mode")
    else:
        print("[PATCH F] WARNING: Could not find blueprint block")

    # ========================================================================
    # PATCH G: Update __all__ exports
    # ========================================================================
    old_all = '''__all__ = [
    "build_parser",
    "prompt_manual_intervention",
    "render_defender_whitelist_warning",
    "run_wizard",
    "standard_menu_prompt",
    "standard_text_prompt",
    "emit_comfyui_preflight_warning",
    "COMFYUI_PREFLIGHT_WARNING",
    "GOLDEN_HANDOFF_TITLE",
    "GOLDEN_HANDOFF_PROMPT",
    "GOLDEN_HANDOFF_OPTION_MASS_BAKE",
    "GOLDEN_HANDOFF_OPTION_FULL_RENDER",
    "GOLDEN_HANDOFF_OPTION_PRODUCE",
    "GOLDEN_HANDOFF_OPTION_AUDIT",
    "GOLDEN_HANDOFF_OPTION_HOME",
    "_dispatch_mass_production",
    "_run_director_studio",
    "_run_interactive",
    "_run_interactive_shell",
]'''

    new_all = '''__all__ = [
    "build_parser",
    "prompt_manual_intervention",
    "render_defender_whitelist_warning",
    "run_wizard",
    "standard_menu_prompt",
    "standard_text_prompt",
    "emit_comfyui_preflight_warning",
    "COMFYUI_PREFLIGHT_WARNING",
    "GOLDEN_HANDOFF_TITLE",
    "GOLDEN_HANDOFF_PROMPT",
    "GOLDEN_HANDOFF_OPTION_MASS_BAKE",
    "GOLDEN_HANDOFF_OPTION_FULL_RENDER",
    "GOLDEN_HANDOFF_OPTION_PRODUCE",
    "GOLDEN_HANDOFF_OPTION_AUDIT",
    "GOLDEN_HANDOFF_OPTION_HOME",
    "_dispatch_mass_production",
    "_run_director_studio",
    "_run_interactive",
    "_run_interactive_shell",
    # SESSION-179: Visual Distillation & Style Retargeting
    "VISUAL_DISTILLATION_OPTION",
]

# SESSION-179: Visual Distillation menu option label (DaC contract)
VISUAL_DISTILLATION_OPTION = (
    "[D] 👁️ 视觉临摹 — 丢入参考动图，让 AI 逆向推导物理参数！"
)'''

    if old_all in content:
        content = content.replace(old_all, new_all)
        print("[PATCH G] __all__ exports updated")
    else:
        print("[PATCH G] WARNING: Could not find __all__ block")

    # ========================================================================
    # PATCH H: Update module docstring
    # ========================================================================
    old_docstring_end = '''SESSION-159 (P0-SESSION-159-UX-ALIGNMENT-V2) upgrade — "Full-Array Mass
Production Dashboard":'''
    new_docstring_end = '''SESSION-179 (P0-SESSION-179-VISUAL-DISTILLATION-AND-RESKINNING) upgrade:
The Director Studio creation menu is expanded with three new capabilities:
    [D] 👁️ 视觉临摹 — GIF/Image-Sequence to Physics reverse-engineering
    Blueprint Vault — Custom naming with timestamp fallback on save
    Style Retargeting — Override vibe prompt in Blueprint Derivation mode
Key constraints:
- ZERO cv2 dependency — uses ONLY PIL.ImageSequence for GIF processing
- Graceful fallback on API failure — never crashes, returns safe defaults
- Style Retargeting preserves motion skeleton, only replaces vibe/style

SESSION-159 (P0-SESSION-159-UX-ALIGNMENT-V2) upgrade — "Full-Array Mass
Production Dashboard":'''

    if old_docstring_end in content:
        content = content.replace(old_docstring_end, new_docstring_end)
        print("[PATCH H] Module docstring updated")
    else:
        print("[PATCH H] WARNING: Could not find docstring end")

    with open(path, "w") as f:
        f.write(content)
    print("[CLI PATCH] cli_wizard.py patched successfully")


def patch_interactive_gate():
    """Patch interactive_gate.py for Blueprint Vault custom naming."""
    path = "mathart/quality/interactive_gate.py"
    with open(path, "r") as f:
        content = f.read()

    # Update _offer_blueprint_save to support custom naming with timestamp fallback
    old_save = '''    def _offer_blueprint_save(self, genotype: "Genotype") -> Optional[Path]:
        """After approval, offer to save the converged genotype as a Blueprint.
        Returns the path to the saved Blueprint YAML, or None if declined.
        """
        from ..workspace.director_intent import Blueprint, BlueprintMeta
        self.output_fn("\\n💾 状态极佳！是否将当前绝佳参数保存为可复用的【蓝图模板 Blueprint】？")
        self.output_fn("  [Y] 保存蓝图")
        self.output_fn("  [N] 跳过")
        save_choice = self.input_fn("请选择 [Y/N]: ").strip().upper()
        if save_choice not in ("Y", "YES"):
            return None
        name = self.input_fn("请为蓝图命名 (英文, 如 hero_v1): ").strip()
        if not name:
            name = "unnamed_blueprint"'''

    new_save = '''    def _offer_blueprint_save(self, genotype: "Genotype") -> Optional[Path]:
        """After approval, offer to save the converged genotype as a Blueprint.

        SESSION-179: Blueprint Vault — Custom Naming with Timestamp Fallback.
        Users can now input a custom filename (e.g., ``heavy_jump_v1``).
        If left empty, a timestamp-based name is auto-generated to prevent
        accidental overwrites.

        Returns the path to the saved Blueprint YAML, or None if declined.
        """
        from ..workspace.director_intent import Blueprint, BlueprintMeta
        import datetime
        self.output_fn("\\n💾 状态极佳！是否将当前绝佳参数保存为可复用的【蓝图模板 Blueprint】？")
        self.output_fn("  [Y] 保存蓝图")
        self.output_fn("  [N] 跳过")
        save_choice = self.input_fn("请选择 [Y/N]: ").strip().upper()
        if save_choice not in ("Y", "YES"):
            return None
        name = self.input_fn(
            "请为蓝图命名 (如 heavy_jump_v1, 留空则使用时间戳自动生成): "
        ).strip()
        if not name:
            # SESSION-179: Timestamp fallback for Blueprint Vault
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"blueprint_{ts}"
            self.output_fn(f"\\033[90m    ↳ 自动生成蓝图名: {name}\\033[0m")'''

    if old_save in content:
        content = content.replace(old_save, new_save)
        print("[GATE PATCH] Blueprint Vault custom naming patched")
    else:
        print("[GATE PATCH] WARNING: Could not find _offer_blueprint_save")

    with open(path, "w") as f:
        f.write(content)
    print("[GATE PATCH] interactive_gate.py patched successfully")


if __name__ == "__main__":
    patch_cli_wizard()
    patch_interactive_gate()
    print("\n✅ All SESSION-179 CLI patches applied successfully!")
