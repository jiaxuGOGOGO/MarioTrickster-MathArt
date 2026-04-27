path = r"e:\unity project\exercise1\MarioTrickster-MathArt\mathart\factory\mass_production.py"
content = open(path, encoding="utf-8").read()

old = (
    'def _sprite_asset_prompts(raw_vibe: str, asset_spec: dict[str, Any]) -> tuple[str, str]:\n'
    '    base_positive = (\n'
    '        "tiny 2D metroidvania character sprite, single full body side-view game character, "\n'
    '        "black background, transparent-ready silhouette, clean pixel art, readable arms and legs, "\n'
    '        "consistent body proportions, consistent ground line, game-ready sprite frame, "\n'
    '        "small character centered in frame, no scenery"\n'
    '    )\n'
)

new = (
    'def _sprite_asset_prompts(raw_vibe: str, asset_spec: dict[str, Any]) -> tuple[str, str]:\n'
    '    base_positive = (\n'
    '        "pixel art, 16-bit game sprite, tiny 2D side-view character, "\n'
    '        "single character full body, transparent background, clean hard pixel edges, "\n'
    '        "limited color palette, flat cel shading, no anti-aliasing, "\n'
    '        "retro game art style, metroidvania sprite, readable silhouette, "\n'
    '        "consistent body proportions, game-ready sprite frame, "\n'
    '        "small character centered in frame, no background, no scenery, "\n'
    '        "indie game character sprite sheet"\n'
    '    )\n'
)

if old not in content:
    print("NOT FOUND")
else:
    content = content.replace(old, new, 1)
    # Also patch negative prompt
    old_neg = (
        '    negative = (\n'
        '        "building, architecture, room, vehicle, machine, cockpit, window, door, large environment, "\n'
        '        "landscape, background scenery, isometric view, top down view, front view, portrait, cropped body, "\n'
        '        "huge character closeup, realistic painting, cinematic scene, text, ui, logo, blurry, deformed, "\n'
        '        "extra limbs, inconsistent character, changing outfit"\n'
        '    )\n'
        '    if asset_spec.get("asset_family") != "character_sprite":\n'
        '        return raw_vibe, "blurry, low quality, distorted, deformed"\n'
        '    return positive, negative\n'
    )
    new_neg = (
        '    negative = (\n'
        '        "photorealistic, realistic texture, noise texture, abstract pattern, "\n'
        '        "building, architecture, room, vehicle, machine, cockpit, window, door, "\n'
        '        "large environment, landscape, background scenery, "\n'
        '        "isometric view, top down view, front view, portrait, cropped body, "\n'
        '        "huge character closeup, realistic painting, oil painting, watercolor, "\n'
        '        "cinematic scene, text, ui, logo, blurry, deformed, extra limbs, "\n'
        '        "inconsistent character, changing outfit, smooth gradient, "\n'
        '        "anti-aliased edges, painterly style, impressionist, "\n'
        '        "high detail texture, noisy, grain, film grain"\n'
        '    )\n'
        '    if asset_spec.get("asset_family") != "character_sprite":\n'
        '        return raw_vibe, "blurry, low quality, distorted, deformed"\n'
        '    return positive, negative\n'
    )
    if old_neg in content:
        content = content.replace(old_neg, new_neg, 1)
        print("prompt+negative OK")
    else:
        print("NEGATIVE NOT FOUND but positive replaced")
    open(path, "w", encoding="utf-8").write(content)
