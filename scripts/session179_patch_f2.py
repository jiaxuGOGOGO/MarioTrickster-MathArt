"""PATCH F2: Style Retargeting - line-based insertion."""

with open("mathart/cli_wizard.py", "rb") as f:
    content = f.read().decode("utf-8")

# Find the exact target
target = 'raw_intent["base_blueprint"] = bp_path\n'
idx = content.find(target)
if idx == -1:
    print("[PATCH F2] ERROR: Could not find target line")
    exit(1)

# Find the next line after target
insert_pos = idx + len(target)

# Check if Style Retargeting already exists
if "Style Retargeting" in content[insert_pos:insert_pos+500]:
    print("[PATCH F2] Style Retargeting already exists, skipping")
    exit(0)

# Build the insertion block
insertion = '''        # ── SESSION-179: Style Retargeting (无缝动静解耦换皮) ──────────
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
'''

content = content[:insert_pos] + insertion + content[insert_pos:]

with open("mathart/cli_wizard.py", "wb") as f:
    f.write(content.encode("utf-8"))

print("[PATCH F2] Style Retargeting inserted successfully")
