"""V6-safe ComfyUI static-asset utility functions.

This module keeps prompt translation, prompt armor, in-memory image upscale,
and static latent canvas normalization independent from the archived AI video
stream backend.
"""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AI_TARGET_RES = 512
_BASE_POSITIVE_PROMPT = (
    "masterpiece, best quality, highly detailed, "
    "3d game character asset, clean white background, "
    "vibrant colors, clear outlines, (masterpiece:1.2)"
)
_BASE_NEGATIVE_PROMPT = (
    "nsfw, worst quality, low quality, bad anatomy, "
    "blurry, noisy, ugly, deformed, extra limbs, "
    "messy background, text, watermark"
)

VIBE_TRANSLATION_MAP: dict[str, str] = {
    "活泼的跳跃": "lively jumping, dynamic energetic motion, vivid, (bouncy:1.2)",
    "夸张的弹性": "exaggerated squash and stretch, extremely bouncy, cartoonish physics, elastic deformation",
    "沉重的落地": "heavy landing, massive impact, rigid weight, dramatic pose",
    "轻盈的跳跃": "light graceful jump, floating motion, featherweight, airy pose",
    "厚重的打击": "heavy powerful strike, massive impact, crushing blow, weighty attack",
    "活泼的弹性": "lively bouncy motion, springy elastic, energetic squash and stretch",
    "沉稳的待机": "calm steady idle, composed standing pose, subtle breathing",
    "夸张的跳跃": "exaggerated dramatic jump, over-the-top leap, extreme hang time",
    "轻盈的跑步": "light graceful running, nimble sprint, effortless forward motion",
    "厚重的落地": "heavy landing, ground-shaking impact, massive weight drop",
    "受击": "getting hit, damaged, impact, recoiling, pain expression",
    "跑": "running fast, sprinting pose, intense forward motion",
    "走": "smooth walking, rhythmic walk cycle, calm pacing",
    "待机": "idle pose, subtle breathing motion, standing still",
    "跳": "jumping, leaping upward, aerial pose, dynamic takeoff",
    "跳跃": "jumping, leaping upward, dynamic takeoff, hang time",
    "攻击": "attacking, combat strike, offensive action, dynamic swing",
    "落地": "landing, touching down, impact absorption, recovery pose",
    "死亡": "death, collapsing, falling down, defeated pose",
    "击飞": "knocked back, launched airborne, ragdoll, tumbling",
    "冲刺": "dashing forward, lunging, charge attack, rapid thrust",
    "翻滚": "rolling, dodge roll, tumbling evasion, acrobatic",
    "格挡": "blocking, defensive guard, shield stance, parry",
    "施法": "casting spell, magic channeling, arcane gesture, glowing",
    "投掷": "throwing, projectile toss, overhand launch",
    "滑行": "sliding, ground slide, low profile dash",
    "攀爬": "climbing, scaling wall, grip and pull, vertical motion",
    "游泳": "swimming, water motion, breaststroke, aquatic pose",
    "活泼": "lively, energetic, dynamic, vivid motion",
    "夸张": "exaggerated, over-the-top, dramatic, cartoonish",
    "沉稳": "calm, steady, composed, grounded, deliberate",
    "轻盈": "light, airy, graceful, featherweight, nimble",
    "厚重": "heavy, weighty, massive, powerful, grounded",
    "弹性": "bouncy, elastic, springy, squash and stretch",
    "沉重": "heavy, weighty, massive, ponderous, slow",
    "可爱": "cute, adorable, chibi-style, round features, kawaii",
    "帅气": "cool, stylish, sharp features, confident pose",
    "狂野": "wild, feral, untamed, aggressive, fierce",
    "优雅": "elegant, graceful, refined, flowing motion",
    "爆裂": "explosive, burst, shockwave, high energy impact",
    "流畅": "fluid, smooth, seamless motion, flowing",
    "精致": "exquisite, finely detailed, polished, premium quality",
    "可怕": "scary, menacing, intimidating, dark aura",
    "神秘": "mysterious, enigmatic, arcane, ethereal glow",
    "机械": "mechanical, robotic, metallic, industrial, mecha",
    "赛博朋克": "cyberpunk, neon lights, futuristic dystopia, high-tech low-life, glowing circuits",
    "赛博朋克风格": "cyberpunk style, neon-lit, futuristic dystopia, high-tech low-life, glowing circuits",
    "像素": "pixel art, retro game sprite, 8-bit style, pixelated",
    "像素风": "pixel art style, retro game sprite, 8-bit aesthetic, pixelated",
    "像素风格": "pixel art style, retro game sprite, 8-bit aesthetic, pixelated",
    "复古": "retro, vintage, classic, old-school, nostalgic",
    "蒸汽朋克": "steampunk, brass gears, victorian machinery, clockwork, steam-powered",
    "奇幻": "fantasy, magical, enchanted, mythical, arcane",
    "科幻": "sci-fi, futuristic, space-age, high-tech, advanced technology",
    "暗黑": "dark, gothic, sinister, shadowy, ominous",
    "卡通": "cartoon, animated, cel-shaded, stylized, toon",
    "写实": "realistic, photorealistic, lifelike, detailed, natural",
    "水墨": "ink wash painting, Chinese ink art, sumi-e, brush stroke, traditional",
    "日系": "anime style, Japanese animation, cel-shaded, manga aesthetic",
    "欧美": "western style, realistic proportions, detailed shading",
    "Q版": "chibi, super-deformed, cute proportions, big head small body",
    "低多边形": "low poly, geometric, faceted, minimalist 3d",
    "霓虹": "neon, glowing, luminous, fluorescent, bright colors",
    "荧光": "fluorescent, glowing, luminous, neon bright",
    "雨": "rain, rainy, rainfall, wet, water droplets",
    "雨中": "in the rain, rainy scene, rainfall, wet environment",
    "在雨中": "in the rain, rainy scene, rainfall, wet environment",
    "雪": "snow, snowy, winter, snowfall, frost",
    "雪中": "in the snow, snowy scene, winter landscape",
    "火": "fire, flames, burning, blazing, inferno",
    "森林": "forest, woodland, trees, lush greenery, nature",
    "城市": "city, urban, cityscape, metropolitan, buildings",
    "夜晚": "night, nighttime, dark sky, moonlight, nocturnal",
    "黄昏": "dusk, sunset, twilight, golden hour, warm sky",
    "黎明": "dawn, sunrise, early morning, first light",
    "海边": "seaside, beach, ocean shore, coastal, waves",
    "太空": "outer space, cosmos, starfield, zero gravity, nebula",
    "地下城": "dungeon, underground, dark cavern, stone walls, torchlit",
    "废墟": "ruins, abandoned, post-apocalyptic, crumbling, overgrown",
    "战场": "battlefield, war zone, combat arena, destruction",
    "奔跑": "running fast, sprinting, dynamic run, forward dash",
    "在雨中奔跑": "running in the rain, sprinting through rainfall, dynamic wet scene",
    "飞行": "flying, airborne, soaring, wings spread, aerial",
    "战斗": "fighting, combat, battle action, martial arts",
    "舞蹈": "dancing, dance pose, rhythmic motion, graceful movement",
    "射击": "shooting, gunfire, aiming, ranged attack",
    "挥剑": "swinging sword, sword slash, blade attack, melee strike",
    "释放魔法": "casting magic, spell release, arcane burst, magical energy",
    "防御": "defending, defensive stance, guard position, shield block",
    "蓄力": "charging up, power accumulation, energy gathering, wind-up",
    "变身": "transformation, morphing, shape-shifting, power-up",
    "战士": "warrior, fighter, armored combatant, battle-ready",
    "法师": "mage, wizard, sorcerer, spellcaster, arcane master",
    "刺客": "assassin, rogue, stealthy, shadow warrior, ninja",
    "骑士": "knight, paladin, armored champion, noble warrior",
    "弓箭手": "archer, bowman, ranger, marksman",
    "忍者": "ninja, shinobi, stealthy warrior, shadow assassin",
    "机器人": "robot, android, mech, mechanical being, cyborg",
    "精灵": "elf, fairy, ethereal being, pointed ears, magical creature",
    "恶魔": "demon, devil, fiend, dark creature, horned",
    "天使": "angel, celestial, winged, divine, holy",
    "龙": "dragon, drake, wyrm, scaled beast, fire-breathing",
    "僵尸": "zombie, undead, shambling, decaying, reanimated",
    "吸血鬼": "vampire, blood-sucker, nocturnal, fanged, pale",
    "海盗": "pirate, buccaneer, swashbuckler, seafarer",
    "超级英雄": "superhero, caped hero, powered being, vigilante",
    "史诗": "epic, grand, magnificent, awe-inspiring, legendary",
    "华丽": "gorgeous, magnificent, splendid, ornate, lavish",
    "简约": "minimalist, simple, clean, understated, refined",
    "恐怖": "horror, terrifying, creepy, nightmarish, dread",
    "温馨": "warm, cozy, heartwarming, gentle, comforting",
    "热血": "passionate, fiery, intense, burning spirit, heroic",
    "酷炫": "cool, awesome, flashy, stylish, impressive",
    "风格": "style, aesthetic, artistic direction",
    "赛博朋克风格的像素在雨中奔跑": "cyberpunk pixel art character running in the rain, neon lights, wet reflections, futuristic dystopia, dynamic sprint",
}


def _translate_vibe(raw_vibe: str) -> str:
    if not raw_vibe or not raw_vibe.strip():
        return ""
    cleaned = raw_vibe.strip()
    if cleaned in VIBE_TRANSLATION_MAP:
        return VIBE_TRANSLATION_MAP[cleaned]
    tokens = re.split(r"[,;，；\s的]+", cleaned)
    translated = [VIBE_TRANSLATION_MAP.get(token.strip(), token.strip()) for token in tokens if token.strip()]
    return ", ".join(translated) if translated else cleaned


def _armor_prompt(user_vibe: str) -> str:
    english_vibe = _translate_vibe(user_vibe)
    return f"{_BASE_POSITIVE_PROMPT}, {english_vibe}" if english_vibe else _BASE_POSITIVE_PROMPT


def _jit_upscale_image(
    image_path: str | Path,
    *,
    is_mask: bool = False,
    matting_color: tuple[int, int, int] | None = None,
) -> bytes:
    from PIL import Image as PILImage

    img = PILImage.open(str(image_path))
    original_size = img.size
    resample = PILImage.NEAREST if is_mask else PILImage.LANCZOS
    img_upscaled = img.resize((AI_TARGET_RES, AI_TARGET_RES), resample=resample)

    if matting_color is not None and img_upscaled.mode in ("RGBA", "LA", "P"):
        bg = PILImage.new("RGB", img_upscaled.size, matting_color)
        if "A" in img_upscaled.getbands():
            bg.paste(img_upscaled, (0, 0), img_upscaled.getchannel("A"))
        else:
            bg.paste(img_upscaled, (0, 0))
        img_upscaled = bg
    elif img_upscaled.mode != "RGB" and not is_mask:
        img_upscaled = img_upscaled.convert("RGB")

    buf = io.BytesIO()
    img_upscaled.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    logger.info(
        "[JIT-Upscale] %s: %sx%s -> %dx%d (%s, matting=%s, %d bytes)",
        Path(image_path).name,
        original_size[0],
        original_size[1],
        AI_TARGET_RES,
        AI_TARGET_RES,
        "NEAREST" if is_mask else "LANCZOS",
        matting_color,
        len(png_bytes),
    )
    return png_bytes


def _force_latent_canvas_512(workflow: dict[str, Any], actual_frames: int | None = None, fps: int | None = None) -> None:
    for node_id, node_data in workflow.items():
        if not isinstance(node_data, dict):
            continue
        class_type = node_data.get("class_type")
        inputs = node_data.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        if class_type == "EmptyLatentImage":
            inputs["width"] = AI_TARGET_RES
            inputs["height"] = AI_TARGET_RES
            if actual_frames is not None:
                inputs["batch_size"] = max(1, min(128, int(actual_frames)))
            logger.info("[StaticCanvas] EmptyLatentImage node %s normalized to %dx%d", node_id, AI_TARGET_RES, AI_TARGET_RES)
        elif class_type == "VideoCombine" and fps is not None:
            inputs["frame_rate"] = int(fps)
        elif class_type == "ControlNetApplyAdvanced":
            strength = float(inputs.get("strength", 1.0))
            start_percent = float(inputs.get("start_percent", 0.0))
            if strength >= 0.8 and start_percent < 0.1:
                inputs["strength"] = max(0.825, min(0.9, strength))
                if float(inputs.get("end_percent", 1.0)) > 0.6:
                    inputs["end_percent"] = 0.55
            elif strength > 0.45:
                inputs["strength"] = 0.45
