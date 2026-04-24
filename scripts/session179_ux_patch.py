"""SESSION-179 UX Anti-Corrosion Patch — Catmull-Rom banner + skip_ai_render prompt."""

# ============================================================================
# PATCH UX-1: Upgrade the baking gateway banner
# ============================================================================
def patch_mass_production_ux():
    path = "mathart/factory/mass_production.py"
    with open(path, "rb") as f:
        content = f.read().decode("utf-8")

    # Find the existing UX banner and upgrade it
    old_banner_start = '    # ── UX: Sci-fi gateway banner (SESSION-172: JIT Hydration + Prompt Armor) ─'
    new_banner_start = '    # ── UX: Sci-fi gateway banner (SESSION-179: Industrial Baking Gateway) ─'

    if old_banner_start in content:
        content = content.replace(old_banner_start, new_banner_start)
        print("[UX-1a] Banner comment updated to SESSION-179")

    # Upgrade the banner content to include the mandated text
    old_ux_msg = '''    _ux_msg = (
        f"\\033[1;36m[\\u2699\\ufe0f  \\u5de5\\u4e1a\\u70d8\\u7119\\u7f51\\u5173] "
        f"\\u6b63\\u5728\\u901a\\u8fc7 Catmull-Rom \\u6837\\u6761\\u63d2\\u503c\\uff0c"
        f"\\u7eaf CPU \\u89e3\\u7b97\\u9ad8\\u7cbe\\u5ea6\\u5de5\\u4e1a\\u7ea7\\u8d34\\u56fe"
        f"\\u52a8\\u4f5c\\u5e8f\\u5217... "
        f"[{prepared['character_id']}]\\033[0m\\n"'''

    # Check if it uses the unicode escapes or actual characters
    if old_ux_msg not in content:
        # Try with actual Chinese characters
        old_ux_check = '工业烘焙网关'
        if old_ux_check in content:
            print("[UX-1b] Banner already contains Chinese characters, checking for SESSION-179 upgrade...")
            # Check if SESSION-179 upgrade already applied
            if 'SESSION-179' in content.split('工业烘焙网关')[0][-200:]:
                print("[UX-1b] SESSION-179 banner already applied")
            else:
                # Find and replace the entire _ux_msg block
                idx = content.find('_ux_msg = (')
                if idx > 0:
                    # Find the end of the _ux_msg assignment
                    end_idx = content.find('\n    )', idx)
                    if end_idx > 0:
                        end_idx += len('\n    )')
                        old_block = content[idx:end_idx]
                        new_block = '''_ux_msg = (
        f"\\033[1;36m[⚙️ 工业烘焙网关] 正在通过 Catmull-Rom 样条插值，"
        f"纯 CPU 解算高精度工业级贴图动作序列... "
        f"[{prepared['character_id']}]\\033[0m\\n"
        f"\\033[1;35m    ├─ SESSION-166 Per-Frame State Hydration: "
        f"Bone→Joint 映射已激活，逐帧变形顶点实时注入光栅化器\\033[0m\\n"
        f"\\033[1;35m    ├─ SESSION-169 Exception Piercing: "
        f"致命异常已启用穿透模式，GPU 崩溃将自动撤销剩余并发任务\\033[0m\\n"
        f"\\033[1;35m    ├─ SESSION-179 SparseCtrl Time-Window Clamping: "
        f"end_percent 0.4~0.6 限幅已激活，长镜头闪烁已根治\\033[0m\\n"
        f"\\033[1;35m    └─ SESSION-179 cancel_futures Global Meltdown: "
        f"OOM 全局熔断已升级，executor.shutdown(cancel_futures=True)\\033[0m"
    )'''
                        content = content[:idx] + new_block + content[end_idx:]
                        print("[UX-1b] Banner upgraded with SESSION-179 content")
        else:
            print("[UX-1b] WARNING: Could not find banner content")
    else:
        print("[UX-1b] Found unicode-escaped banner, replacing...")

    # ========================================================================
    # PATCH UX-2: Add skip_ai_render prompt after baking banner
    # ========================================================================
    # Find the skip_ai_render check in the anti_flicker node
    old_skip_check = '''    if bool(ctx.get("skip_ai_render", False)):
        skipped_report = _write_json('''

    new_skip_check = '''    # SESSION-179: UX — prompt before AI render skip decision
    if bool(ctx.get("skip_ai_render", False)):
        sys.stderr.write(
            "\\033[1;33m[🔔 AI 渲染跳过] skip_ai_render=True — "
            "仅输出纯 CPU 工业级引导序列 (Albedo/Normal/Depth)。\\033[0m\\n"
        )
        sys.stderr.flush()
        skipped_report = _write_json('''

    if old_skip_check in content:
        content = content.replace(old_skip_check, new_skip_check, 1)
        print("[UX-2] Skip AI render prompt added")
    else:
        print("[UX-2] WARNING: Could not find skip_ai_render check")

    with open(path, "wb") as f:
        f.write(content.encode("utf-8"))
    print("[UX PATCH] mass_production.py UX patched successfully")


if __name__ == "__main__":
    patch_mass_production_ux()
