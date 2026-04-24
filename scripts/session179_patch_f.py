"""PATCH F: Style Retargeting in Blueprint Derivation mode [B]."""

with open("mathart/cli_wizard.py", "r") as f:
    content = f.read()

# Find the exact block for blueprint derivation
old_block = '''        raw_intent["base_blueprint"] = bp_path
        variants_str = standard_text_prompt('''

new_block = '''        raw_intent["base_blueprint"] = bp_path
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
        variants_str = standard_text_prompt('''

if old_block in content:
    content = content.replace(old_block, new_block, 1)
    print("[PATCH F] Style Retargeting added to Blueprint Derivation mode")
else:
    print("[PATCH F] WARNING: Could not find exact block")

with open("mathart/cli_wizard.py", "w") as f:
    f.write(content)
