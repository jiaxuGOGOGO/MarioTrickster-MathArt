"""PATCH GATE: Blueprint Vault custom naming in interactive_gate.py."""

with open("mathart/quality/interactive_gate.py", "rb") as f:
    content = f.read().decode("utf-8")

# Find the _offer_blueprint_save method
old_name_prompt = '请为蓝图命名 (英文, 如 hero_v1): '
new_name_block_marker = 'Blueprint Vault'

if new_name_block_marker in content:
    print("[GATE PATCH] Blueprint Vault already exists, skipping")
    exit(0)

if old_name_prompt in content:
    # Replace the naming prompt with Blueprint Vault version
    old_section = '''        name = self.input_fn("请为蓝图命名 (英文, 如 hero_v1): ").strip()
        if not name:
            name = "unnamed_blueprint"'''
    
    new_section = '''        # SESSION-179: Blueprint Vault — Custom Naming with Timestamp Fallback
        name = self.input_fn(
            "请为蓝图命名 (如 heavy_jump_v1, 留空则使用时间戳自动生成): "
        ).strip()
        if not name:
            import datetime
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"blueprint_{ts}"
            self.output_fn(f"\\033[90m    ↳ 自动生成蓝图名: {name}\\033[0m")'''
    
    if old_section in content:
        content = content.replace(old_section, new_section)
        print("[GATE PATCH] Blueprint Vault custom naming patched (exact match)")
    else:
        # Try a more flexible approach - just replace the prompt line
        content = content.replace(
            old_name_prompt,
            '请为蓝图命名 (如 heavy_jump_v1, 留空则使用时间戳自动生成): '
        )
        # Also replace the unnamed fallback
        content = content.replace(
            '            name = "unnamed_blueprint"',
            '''            import datetime
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"blueprint_{ts}"
            self.output_fn(f"\\033[90m    ↳ Blueprint Vault 自动生成蓝图名: {name}\\033[0m")'''
        )
        print("[GATE PATCH] Blueprint Vault custom naming patched (flexible match)")
else:
    print("[GATE PATCH] WARNING: Could not find naming prompt")

with open("mathart/quality/interactive_gate.py", "wb") as f:
    f.write(content.encode("utf-8"))
