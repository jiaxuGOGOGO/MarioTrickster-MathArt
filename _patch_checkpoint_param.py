path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

# Add comfyui_checkpoint param to run_mass_production_factory
old_param = '    ai_render_min_vram_free_bytes: int = 6 * 1024 * 1024 * 1024,\n    ai_render_min_ram_free_bytes: int = 4 * 1024 * 1024 * 1024,\n    sprite_asset_mode: bool = True,'
new_param = '    ai_render_min_vram_free_bytes: int = 6 * 1024 * 1024 * 1024,\n    ai_render_min_ram_free_bytes: int = 4 * 1024 * 1024 * 1024,\n    comfyui_checkpoint: str = "",\n    sprite_asset_mode: bool = True,'
if old_param not in content:
    print("param NOT FOUND")
else:
    content = content.replace(old_param, new_param, 1)
    print("param OK")

# Pass comfyui_checkpoint to initial_context
old_ctx = '            "ai_render_min_vram_free_bytes": max(1, int(ai_render_min_vram_free_bytes)),\n            "ai_render_min_ram_free_bytes": max(1, int(ai_render_min_ram_free_bytes)),\n            "sprite_asset_mode": bool(sprite_asset_mode),'
new_ctx = '            "ai_render_min_vram_free_bytes": max(1, int(ai_render_min_vram_free_bytes)),\n            "ai_render_min_ram_free_bytes": max(1, int(ai_render_min_ram_free_bytes)),\n            "comfyui_checkpoint": str(comfyui_checkpoint or ""),\n            "sprite_asset_mode": bool(sprite_asset_mode),'
if old_ctx not in content:
    print("ctx NOT FOUND")
else:
    content = content.replace(old_ctx, new_ctx, 1)
    print("ctx OK")

# Pass comfyui_checkpoint into _render_cfg comfyui block
old_render = '            "connect_timeout": float(ctx.get("ai_render_connect_timeout", 5.0)),\n            "steps": max(4, min(int(ctx.get("ai_render_steps", 12)), 20)),'
new_render = '            "connect_timeout": float(ctx.get("ai_render_connect_timeout", 5.0)),\n            "comfyui_checkpoint": str(ctx.get("comfyui_checkpoint") or ""),\n            "steps": max(4, min(int(ctx.get("ai_render_steps", 12)), 20)),'
if old_render not in content:
    print("render_cfg NOT FOUND")
else:
    content = content.replace(old_render, new_render, 1)
    print("render_cfg OK")

open(path, "w", encoding="utf-8").write(content)
