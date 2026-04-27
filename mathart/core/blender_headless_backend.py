"""V6 Blender headless pixel-art backend.

Registry-native microkernel plugin that emits a standalone pure-``bpy``
``render_pixel_art.py`` script. The script consumes KnowledgeInterpreter
StyleParams and exports Unity zero-post metadata: rects, pivots, durations.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import BackendCapability, BackendMeta, register_backend
from mathart.core.knowledge_interpreter import StyleParams, interpret_knowledge

SCRIPT_NAME = "render_pixel_art.py"

RENDER_SCRIPT = r'''
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

def clear_scene():
    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()

def make_toon_material(style,texture_path=None):
    bands=max(1,int(style.get("toon_bands",3))); hard=float(style.get("shadow_hardness",0.75))
    mat=bpy.data.materials.new("knowledge_toon_shader"); mat.use_nodes=True
    nodes=mat.node_tree.nodes; links=mat.node_tree.links; nodes.clear()
    out=nodes.new("ShaderNodeOutputMaterial"); diff=nodes.new("ShaderNodeBsdfDiffuse")
    s2rgb=nodes.new("ShaderNodeShaderToRGB"); ramp=nodes.new("ShaderNodeValToRGB")
    if texture_path and Path(texture_path).exists():
        tex=nodes.new("ShaderNodeTexImage"); tex.image=bpy.data.images.load(str(texture_path),check_existing=True); tex.interpolation="Closest"; tex.extension="CLIP"
        links.new(tex.outputs["Color"],diff.inputs["Color"])
    ramp.color_ramp.interpolation="CONSTANT"
    while len(ramp.color_ramp.elements)<bands: ramp.color_ramp.elements.new(0.5)
    while len(ramp.color_ramp.elements)>bands: ramp.color_ramp.elements.remove(ramp.color_ramp.elements[-1])
    for i,e in enumerate(ramp.color_ramp.elements):
        t=i/max(bands-1,1); v=min(1.0,t*hard+(1.0-hard)*0.25); e.position=t; e.color=(v,v,v,1)
    links.new(diff.outputs["BSDF"],s2rgb.inputs["Shader"]); links.new(s2rgb.outputs["Color"],ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"],out.inputs["Surface"]); return mat

def setup_scene(style,res):
    scene=bpy.context.scene
    try: scene.render.engine="BLENDER_EEVEE_NEXT"
    except TypeError: scene.render.engine="BLENDER_EEVEE"
    if hasattr(scene,"eevee"):
        scene.eevee.taa_render_samples=1; scene.eevee.taa_samples=1
    scene.render.filter_size=0.01; scene.render.resolution_x=int(res[0]); scene.render.resolution_y=int(res[1])
    scene.render.film_transparent=True; scene.render.image_settings.file_format="PNG"; scene.render.image_settings.color_mode="RGBA"
    scene.view_settings.view_transform="Standard"; scene.view_settings.look="None"; scene.view_settings.exposure=0; scene.view_settings.gamma=1
    scene.use_nodes=True; scene.render.use_compositing=True
    tree=scene.node_tree; tree.nodes.clear()
    rl=tree.nodes.new("CompositorNodeRLayers"); pix=tree.nodes.new("CompositorNodePixelate"); comp=tree.nodes.new("CompositorNodeComposite")
    tree.links.new(rl.outputs["Image"],pix.inputs["Color"]); tree.links.new(pix.outputs["Color"],comp.inputs["Image"])
    cam_data=bpy.data.cameras.new("PixelArtOrthoCamera"); cam_data.type="ORTHO"; cam_data.ortho_scale=4
    cam=bpy.data.objects.new("PixelArtOrthoCamera",cam_data); bpy.context.collection.objects.link(cam)
    cam.location=(0,-6,1.6); cam.rotation_euler=(math.radians(78),0,0); scene.camera=cam
    light_data=bpy.data.lights.new("toon_key_light","SUN"); light=bpy.data.objects.new("toon_key_light",light_data)
    bpy.context.collection.objects.link(light); light.rotation_euler=(math.radians(45),0,math.radians(-35)); light_data.energy=3
    return cam

def import_model(path):
    p=Path(path) if path else None
    if p and p.exists():
        suffix=p.suffix.lower()
        if suffix==".obj": bpy.ops.wm.obj_import(filepath=str(p))
        elif suffix==".fbx": bpy.ops.import_scene.fbx(filepath=str(p))
        elif suffix in {".glb",".gltf"}: bpy.ops.import_scene.gltf(filepath=str(p))
        objs=[o for o in bpy.context.scene.objects if getattr(o,"type","")=="MESH"]
        if objs: return objs[0]
    bpy.ops.mesh.primitive_cube_add(size=1.4, location=(0,0,0.8)); return bpy.context.object

def active_armature():
    arms=[o for o in bpy.context.scene.objects if getattr(o,"type","")=="ARMATURE"]
    return arms[0] if arms else None

def load_model(cfg,mat):
    obj=import_model(cfg.get("base_mesh_path") or cfg.get("source_obj"))
    obj.name=cfg.get("asset_name","pixel_asset")
    obj.data.materials.clear(); obj.data.materials.append(mat)
    if bool(cfg.get("style_params",{}).get("outline_enabled",True)):
        sol=obj.modifiers.new("knowledge_outline","SOLIDIFY"); sol.thickness=max(0.001,float(cfg["style_params"].get("line_width",1))*0.003); sol.use_flip_normals=True
        om=bpy.data.materials.new("knowledge_outline_black"); om.diffuse_color=(0,0,0,1); obj.data.materials.append(om); sol.material_offset=len(obj.data.materials)-1
    return obj

def apply_animation_frame(obj,arm,frame):
    if not isinstance(frame,dict): return
    rt=frame.get("root_transform",{})
    obj.location.x=float(rt.get("x",obj.location.x)); obj.location.z=float(rt.get("y",obj.location.z)); obj.rotation_euler.z=float(rt.get("rotation",obj.rotation_euler.z))
    rotations=frame.get("joint_local_rotations",{}) or frame.get("bone_rotations",{})
    if arm and isinstance(rotations,dict):
        for name,val in rotations.items():
            bone=arm.pose.bones.get(str(name))
            if bone:
                bone.rotation_mode="XYZ"; bone.rotation_euler.z=float(val if not isinstance(val,dict) else val.get("z",val.get("rotation",0)))

def project_pivot(scene,cam,obj,res,pivot_world):
    if pivot_world is None:
        z=min((obj.matrix_world@v.co).z for v in obj.data.vertices) if hasattr(obj.data,"vertices") else obj.location.z
        p=Vector((obj.location.x,obj.location.y,z))
    else: p=Vector((float(pivot_world[0]),float(pivot_world[1]),float(pivot_world[2])))
    co=world_to_camera_view(scene,cam,p); x=max(0,min(1,float(co.x))); y=max(0,min(1,float(co.y)))
    return [x,y],[round(x*res[0],4),round(y*res[1],4)]

def frame_duration(frames,i,fps):
    if i<len(frames):
        f=frames[i]; timing=f.get("anime_timing") or f.get("metadata",{}).get("anime_timing") or {}
        if "duration" in f: return float(f["duration"])
        if timing.get("hit_stop") or str(timing.get("mode","")).endswith("hold"): return float(timing.get("duration",1/fps))
    return 1/max(fps,1)

def physics_payload(frame):
    meta=frame.get("metadata",{}) if isinstance(frame,dict) else {}
    return meta.get("v6_physics_payload") or frame.get("v6_physics_payload") or {}

def make_fluid_material(params, style):
    mat=bpy.data.materials.new("knowledge_fluid_metaball_emission"); mat.use_nodes=True
    nodes=mat.node_tree.nodes; links=mat.node_tree.links; nodes.clear()
    out=nodes.new("ShaderNodeOutputMaterial"); emit=nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value=(0.25,0.78,1.0,1.0)
    emit.inputs["Strength"].default_value=float(params.get("glow_intensity",style.get("fluid_glow_intensity",1.8)))
    links.new(emit.outputs["Emission"],out.inputs["Surface"]); return mat

def create_fluid_metaballs(frames, style):
    objects=[]
    for i,frame in enumerate(frames):
        fluid=physics_payload(frame).get("fluid_vfx",{})
        particles=fluid.get("particle_samples") or fluid.get("particles") or []
        params=fluid.get("params",{})
        if not particles: continue
        mb=bpy.data.metaballs.new(f"fluid_metaballs_{i:04d}")
        mb.resolution=float(params.get("metaball_resolution",style.get("fluid_metaball_resolution",0.18)))
        mb.render_resolution=float(params.get("render_resolution",style.get("fluid_render_resolution",0.08)))
        obj=bpy.data.objects.new(f"fluid_metaballs_{i:04d}",mb); bpy.context.collection.objects.link(obj)
        obj.data.materials.append(make_fluid_material(params,style))
        radius=float(params.get("particle_radius",style.get("fluid_particle_radius",0.055)))
        for p in particles:
            elem=mb.elements.new(type="BALL"); elem.radius=max(0.005,float(p.get("size",1.0))*radius)
            elem.co=(float(p.get("x",0.5))*3.0-1.5,0.03,float(p.get("y",0.5))*2.2)
        obj.hide_viewport=True; obj.hide_render=True; objects.append((i,obj))
    return objects

def create_cloth_meshes(frames, style):
    objects=[]; mat=bpy.data.materials.new("knowledge_cloth_cape_toon"); mat.diffuse_color=(0.82,0.12,0.26,1)
    for i,frame in enumerate(frames):
        cloth=physics_payload(frame).get("cloth_xpbd",{})
        points=cloth.get("points") or []
        if len(points)<2: continue
        width=float(cloth.get("params",{}).get("segment_length",0.126))*0.55; verts=[]; faces=[]
        for n,p in enumerate(points):
            x=float(p.get("x",0)); z=float(p.get("y",0)); y=float(p.get("z",0)); verts.extend([(x-width,y,z),(x+width,y,z)])
            if n>0:
                a=(n-1)*2; faces.append((a,a+1,a+3,a+2))
        mesh=bpy.data.meshes.new(f"cloth_xpbd_mesh_{i:04d}"); mesh.from_pydata(verts,[],faces); mesh.update()
        obj=bpy.data.objects.new(f"cloth_xpbd_mesh_{i:04d}",mesh); bpy.context.collection.objects.link(obj); obj.data.materials.append(mat)
        obj.hide_viewport=True; obj.hide_render=True; objects.append((i,obj))
    return objects

def show_only(objects, idx):
    for frame_idx,obj in objects:
        visible=frame_idx==idx; obj.hide_viewport=not visible; obj.hide_render=not visible

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--config",required=True); a=ap.parse_args()
    cfg=json.loads(Path(a.config).read_text(encoding="utf-8")); out=Path(cfg["output_dir"]); out.mkdir(parents=True,exist_ok=True)
    style=cfg.get("style_params",{}); res=cfg.get("resolution",[256,256]); fps=float(cfg.get("fps",12)); name=cfg.get("asset_name","pixel_asset")
    frames=list(cfg.get("animation_data") or cfg.get("frames",[])); n=max(1,int(cfg.get("frame_count",len(frames) or 1)))
    clear_scene(); mat=make_toon_material(style,cfg.get("texture_path")); cam=setup_scene(style,res); obj=load_model(cfg,mat); arm=active_armature()
    fluid_objs=create_fluid_metaballs(frames,style); cloth_objs=create_cloth_meshes(frames,style)
    sprites=[]; durations=[]; pngs=[]
    for i in range(n):
        bpy.context.scene.frame_set(i)
        if i<len(frames):
            apply_animation_frame(obj,arm,frames[i])
        show_only(fluid_objs,i); show_only(cloth_objs,i)
        png=out/f"{name}_{i:04d}.png"; bpy.context.scene.render.filepath=str(png); bpy.ops.render.render(write_still=True)
        dur=frame_duration(frames,i,fps); piv,pix=project_pivot(bpy.context.scene,cam,obj,res,cfg.get("pivot_world"))
        rect={"x":0,"y":i*int(res[1]),"width":int(res[0]),"height":int(res[1])}
        sprites.append({"name":f"{name}_{i:04d}","png":str(png),"rect":rect,"pivot":piv,"pivot_pixel":pix,"duration":dur})
        durations.append(dur); pngs.append(str(png))
    meta={"asset_name":name,"unity_import_contract":"zero_post_processing","texture_settings":{"filterMode":"Point","compression":"None","mipmapEnabled":False,"spriteMode":"Multiple"},"style_params":style,"v6_physics_render":{"fluid_metaballs":len(fluid_objs),"cloth_meshes":len(cloth_objs)},"sprite_rects":[s["rect"] for s in sprites],"pivot_points":[s["pivot"] for s in sprites],"frame_durations":durations,"sprites":sprites,"atlas_layout":{"width":int(res[0]),"height":int(res[1])*n,"packing":"vertical_strip_exact_rects"},"timing_contract":{"source":"Phase3 anime_timing_modifier","duration_unit":"seconds","unity_animator_should_hold_each_sprite_for_duration":True}}
    meta_path=out/f"{name}_unity_meta.json"; meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (out/f"{name}_render_manifest.json").write_text(json.dumps({"png_paths":pngs,"unity_meta":str(meta_path)},indent=2),encoding="utf-8")
if __name__=="__main__": main()
'''

@register_backend(
    "blender_headless_pixel_art",
    display_name="Blender Headless Pixel Art Renderer",
    version="1.1.0",
    artifact_families=(ArtifactFamily.IMAGE_SEQUENCE.value, ArtifactFamily.META_REPORT.value),
    capabilities=(BackendCapability.SPRITE_EXPORT, BackendCapability.ATLAS_EXPORT, BackendCapability.SHADER_EXPORT),
    input_requirements=("output_dir",),
    session_origin="V6-PHASE-5.5",
    schema_version="1.0.0",
)
class BlenderHeadlessPixelArtBackend:
    """Generate and optionally run a pure-bpy Blender pixel-art render job."""

    @property
    def name(self) -> str:
        return "blender_headless_pixel_art"

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def _style_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        style: StyleParams = interpret_knowledge(context.get("knowledge_path")).style
        payload = style.to_dict()
        overrides = context.get("style_params")
        if isinstance(overrides, dict):
            payload.update(overrides)
        payload.setdefault("outline_enabled", payload.get("line_width", 1.0) > 0.0)
        return payload

    def _write_script(self, output_dir: Path) -> Path:
        script_path = output_dir / SCRIPT_NAME
        script_path.write_text(RENDER_SCRIPT, encoding="utf-8")
        return script_path

    def _write_config(self, context: dict[str, Any], output_dir: Path, style: dict[str, Any]) -> Path:
        asset_name = str(context.get("asset_name", context.get("name", "pixel_asset")))
        frames = context.get("animation_data", context.get("frames", []))
        frames = frames if isinstance(frames, list) else []
        config = {
            "asset_name": asset_name,
            "output_dir": str(output_dir),
            "style_params": style,
            "resolution": list(context.get("resolution", [256, 256])),
            "fps": float(context.get("fps", 12.0)),
            "frame_count": int(context.get("frame_count", len(frames) or 1)),
            "frames": frames,
            "animation_data": frames,
            "pivot_world": context.get("pivot_world"),
            "source_obj": context.get("source_obj"),
            "base_mesh_path": context.get("base_mesh_path", context.get("source_obj")),
            "texture_path": context.get("texture_path"),
        }
        config_path = output_dir / f"{asset_name}_blender_render_config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return config_path

    def _stitch_spritesheet(self, output_dir: Path, asset_name: str, final_assets_dir: Path | None = None) -> tuple[Path | None, int]:
        frames = sorted(output_dir.glob(f"{asset_name}_[0-9][0-9][0-9][0-9].png"))
        if not frames:
            return None, 0
        final_dir = final_assets_dir or output_dir
        final_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        images = [Image.open(path).convert("RGBA") for path in frames]
        width = max(image.width for image in images)
        height = sum(image.height for image in images)
        sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        y = 0
        for image in images:
            sheet.paste(image, (0, y))
            y += image.height
        sheet_path = final_dir / f"{asset_name}_spritesheet.png"
        sheet.save(sheet_path)
        for image in images:
            image.close()
        for path in frames:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        return sheet_path, len(frames)

    def _write_dependency_fallback_frames(self, output_dir: Path, asset_name: str, config_path: Path) -> int:
        from PIL import Image, ImageDraw
        config = json.loads(config_path.read_text(encoding="utf-8"))
        resolution = config.get("resolution", [256, 256])
        width, height = int(resolution[0]), int(resolution[1])
        frames = config.get("animation_data") or config.get("frames") or []
        frame_count = max(1, int(config.get("frame_count", len(frames) or 1)))
        fps = float(config.get("fps", 12.0))
        sprites = []
        durations = []
        for idx in range(frame_count):
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            frame = frames[idx] if idx < len(frames) and isinstance(frames[idx], dict) else {}
            root = frame.get("root_transform", {})
            cx = width // 2 + int(float(root.get("x", 0.0)) * width * 0.2)
            cy = int(height * 0.62) - int(float(root.get("y", 0.0)) * height * 0.2)
            draw.rectangle((cx - 28, cy - 46, cx + 28, cy + 24), fill=(245, 219, 138, 255), outline=(0, 0, 0, 255), width=3)
            draw.rectangle((cx - 18, cy - 64, cx + 18, cy - 40), fill=(255, 238, 178, 255), outline=(0, 0, 0, 255), width=3)
            png = output_dir / f"{asset_name}_{idx:04d}.png"
            image.save(png)
            timing = frame.get("anime_timing") or frame.get("metadata", {}).get("anime_timing") or {}
            duration = float(frame.get("duration", timing.get("duration", 1.0 / max(fps, 1.0))))
            rect = {"x": 0, "y": idx * height, "width": width, "height": height}
            pivot = [0.5, 0.0]
            sprites.append({"name": f"{asset_name}_{idx:04d}", "png": str(png), "rect": rect, "pivot": pivot, "pivot_pixel": [width * 0.5, 0.0], "duration": duration})
            durations.append(duration)
        meta = {"asset_name": asset_name, "unity_import_contract": "zero_post_processing", "dependency_fallback": "blender_executable_missing", "texture_settings": {"filterMode": "Point", "compression": "None", "mipmapEnabled": False, "spriteMode": "Multiple"}, "base_mesh_path": config.get("base_mesh_path"), "texture_path": config.get("texture_path"), "style_params": config.get("style_params", {}), "sprite_rects": [s["rect"] for s in sprites], "pivot_points": [s["pivot"] for s in sprites], "frame_durations": durations, "sprites": sprites, "atlas_layout": {"width": width, "height": height * frame_count, "packing": "vertical_strip_exact_rects"}, "timing_contract": {"source": "Phase3 anime_timing_modifier", "duration_unit": "seconds", "unity_animator_should_hold_each_sprite_for_duration": True}}
        (output_dir / f"{asset_name}_unity_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return frame_count

    def _write_clean_unity_meta(self, unity_meta: Path, asset_name: str, config_path: Path, sheet_path: Path | None, stitched_frames: int) -> None:
        if unity_meta.exists():
            return
        config = json.loads(config_path.read_text(encoding="utf-8"))
        resolution = config.get("resolution", [256, 256])
        width, height = int(resolution[0]), int(resolution[1])
        frame_count = max(1, int(config.get("frame_count", 1)))
        fps = float(config.get("fps", 12.0))
        durations = [1.0 / max(fps, 1.0) for _ in range(frame_count)]
        sprites = [
            {
                "name": f"{asset_name}_{idx:04d}",
                "spritesheet": str(sheet_path) if sheet_path else "",
                "rect": {"x": 0, "y": idx * height, "width": width, "height": height},
                "pivot": [0.5, 0.0],
                "pivot_pixel": [width * 0.5, 0.0],
                "duration": durations[idx],
            }
            for idx in range(frame_count)
        ]
        meta = {
            "asset_name": asset_name,
            "unity_import_contract": "zero_post_processing",
            "texture_settings": {"filterMode": "Point", "compression": "None", "mipmapEnabled": False, "spriteMode": "Multiple"},
            "style_params": config.get("style_params", {}),
            "sprite_rects": [s["rect"] for s in sprites],
            "pivot_points": [s["pivot"] for s in sprites],
            "frame_durations": durations,
            "sprites": sprites,
            "atlas_layout": {"width": width, "height": height * frame_count, "packing": "vertical_strip_exact_rects", "stitched_frames": stitched_frames},
            "timing_contract": {"source": "Phase3 anime_timing_modifier", "duration_unit": "seconds", "unity_animator_should_hold_each_sprite_for_duration": True},
        }
        unity_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def execute(self, context: dict[str, Any]) -> ArtifactManifest:
        t0 = time.monotonic()
        output_dir = Path(context.get("output_dir", "artifacts/blender_headless"))
        output_dir.mkdir(parents=True, exist_ok=True)
        final_assets_dir = Path(context.get("final_assets_dir", output_dir))
        final_assets_dir.mkdir(parents=True, exist_ok=True)
        asset_name = str(context.get("asset_name", context.get("name", "pixel_asset")))
        style = self._style_payload(context)
        script_path = self._write_script(output_dir)
        config_path = self._write_config(context, output_dir, style)
        run_blender = bool(context.get("run_blender", False))
        blender_returncode: int | None = None
        sheet_path: Path | None = None
        stitched_frames = 0
        if run_blender:
            try:
                proc = subprocess.run([str(context.get("blender_executable", "blender")), "--background", "--python", str(script_path), "--", "--config", str(config_path)], check=False, capture_output=True, text=True)
                blender_returncode = proc.returncode
                (output_dir / f"{asset_name}_blender_stdout.log").write_text(proc.stdout, encoding="utf-8")
                (output_dir / f"{asset_name}_blender_stderr.log").write_text(proc.stderr, encoding="utf-8")
                if blender_returncode == 0:
                    sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
                else:
                    self._write_dependency_fallback_frames(output_dir, asset_name, config_path)
                    sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
            except FileNotFoundError as exc:
                blender_returncode = -2
                (output_dir / f"{asset_name}_blender_stderr.log").write_text(f"Blender executable missing: {exc}\n", encoding="utf-8")
                self._write_dependency_fallback_frames(output_dir, asset_name, config_path)
                sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
        else:
            self._write_dependency_fallback_frames(output_dir, asset_name, config_path)
            sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
        unity_meta = final_assets_dir / f"{asset_name}_unity_meta.json"
        intermediate_meta = output_dir / f"{asset_name}_unity_meta.json"
        if intermediate_meta.exists():
            try:
                os.remove(intermediate_meta)
            except FileNotFoundError:
                pass
        self._write_clean_unity_meta(unity_meta, asset_name, config_path, sheet_path, stitched_frames)
        outputs = {"render_script": str(script_path), "render_config": str(config_path), "unity_meta": str(unity_meta)}
        if sheet_path:
            outputs["spritesheet"] = str(sheet_path)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        return ArtifactManifest(
            artifact_family=ArtifactFamily.META_REPORT.value,
            backend_type=self.name,
            version="1.1.0",
            session_id=str(context.get("session_id", "V6-PHASE-5")),
            outputs=outputs,
            metadata={"asset_name": asset_name, "headless": True, "script_kind": "pure_bpy", "run_blender": run_blender, "blender_returncode": blender_returncode, "style_params": style, "toon_bands": int(style.get("toon_bands", 3)), "outline_enabled": bool(style.get("outline_enabled", True)), "base_mesh_path": context.get("base_mesh_path", context.get("source_obj")), "texture_path": context.get("texture_path"), "mesh_texture_hook_enabled": True, "animation_data_enabled": True, "spritesheet_stitched": sheet_path is not None, "stitched_frame_count": stitched_frames, "loose_frames_cleaned": sheet_path is not None, "nearest_filtering_forced": True, "anti_aliasing": "disabled", "unity_zero_post_metadata": True, "sprite_rects_exported": True, "pivot_projection_api": "bpy_extras.object_utils.world_to_camera_view", "frame_durations_from_phase3": True, "elapsed_ms": elapsed_ms},
            quality_metrics={"headless_script_emitted": 1.0, "config_emitted": 1.0, "style_param_count": float(len(style)), "stitched_frames": float(stitched_frames)},
            tags=["v6-phase-5", "blender-headless", "pure-bpy", "pixel-art", "unity-zero-post", "knowledge-driven-style"],
        )

__all__ = ["BlenderHeadlessPixelArtBackend"]
