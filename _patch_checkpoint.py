path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\core\builtin_backends.py"
content = open(path, encoding="utf-8").read()
old = '        comfyui.setdefault("model_checkpoint", "v1-5-pruned-emaonly.safetensors")'
new = '        # Allow caller to pass comfyui_checkpoint to use pixel-art specialized models\n        comfyui.setdefault("model_checkpoint", comfyui.get("comfyui_checkpoint") or "v1-5-pruned-emaonly.safetensors")'
if old not in content:
    print("NOT FOUND")
else:
    open(path, "w", encoding="utf-8").write(content.replace(old, new, 1))
    print("OK")
