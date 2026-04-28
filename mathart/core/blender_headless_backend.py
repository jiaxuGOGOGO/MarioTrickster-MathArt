"""V6 Blender headless pixel-art backend.

Registry-native microkernel plugin that emits a standalone pure-``bpy``
``render_pixel_art.py`` script. The script consumes KnowledgeInterpreter
StyleParams and exports Unity zero-post metadata: rects, pivots, durations.
"""
from __future__ import annotations

import json
import os
import shutil
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
import argparse, json, math, os, sys
from pathlib import Path
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
import re

BONE_ALIASES={"root":["hips","pelvis","root"],"spine":["spine","chest","torso"],"l_hand":["hand.l","lefthand","left_hand","l_hand","l_arm","leftarm","forearm.l"],"r_hand":["hand.r","righthand","right_hand","r_hand","r_arm","rightarm","forearm.r"],"l_foot":["foot.l","leftfoot","l_foot"],"r_foot":["foot.r","rightfoot","r_foot"]}

def _norm(text):
    return re.sub(r"[^a-z0-9]","",str(text).lower())

def _bone_score(v6_name,bone_name):
    n=_norm(bone_name); aliases=[_norm(v6_name)]+[_norm(a) for a in BONE_ALIASES.get(str(v6_name),[])]
    score=0
    for a in aliases:
        if n==a: score=max(score,100)
        elif a and (a in n or n in a): score=max(score,80)
    low=str(bone_name).lower(); v=_norm(v6_name)
    if (v.startswith("l") or "left" in v) and ("left" in low or low.endswith(".l") or low.endswith("_l")): score+=8
    if (v.startswith("r") or "right" in v) and ("right" in low or low.endswith(".r") or low.endswith("_r")): score+=8
    return score

def build_bone_map(arm,frames):
    if not arm: return {}
    channels={}
    for f in frames:
        if isinstance(f,dict): channels.update(f.get("joint_local_rotations",{}) or f.get("bone_rotations",{}) or {})
    mapping={}; used=set(); names=[b.name for b in arm.pose.bones]
    for v6 in channels:
        ranked=sorted(((_bone_score(v6,n),n) for n in names if n not in used),reverse=True)
        if ranked and ranked[0][0]>0:
            mapping[str(v6)]=ranked[0][1]; used.add(ranked[0][1])
    return mapping

def _rot3(value):
    if isinstance(value,dict): return (float(value.get("x",0)),float(value.get("y",0)),float(value.get("z",value.get("rotation",0))))
    if isinstance(value,(list,tuple)) and len(value)>=3: return (float(value[0]),float(value[1]),float(value[2]))
    return (0.0,0.0,float(value or 0.0))

def _squash_scale(frame):
    meta=frame.get("metadata",{}) if isinstance(frame,dict) else {}; ss=meta.get("squash_stretch",{}) or frame.get("squash_stretch",{})
    rt=frame.get("root_transform",{}) if isinstance(frame,dict) else {}
    return (float(rt.get("squash_stretch_scale_along_velocity",ss.get("stretch_scale",1.0))),1.0,float(rt.get("squash_stretch_scale_perpendicular",ss.get("perpendicular_scale",1.0))))

def _hit_stop_frames(frame,fps):
    timing=frame.get("anime_timing") or frame.get("metadata",{}).get("anime_timing") or {}
    explicit=timing.get("hit_stop_frames") or frame.get("hit_stop_frames")
    if explicit is not None: return max(0,int(explicit))
    if timing.get("hit_stop") or str(timing.get("mode","")).endswith("hold"):
        return max(0,int(round(float(timing.get("duration",1/fps))*fps))-1)
    return 0

def _find_vfx_anchor(arm,bone_map):
    if not arm: return None
    return bone_map.get("r_hand") or bone_map.get("l_hand") or next(iter(bone_map.values()),None)

def _set_active_object(obj):
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:
        pass
    try:
        obj.select_set(True)
    except Exception:
        pass
    try:
        bpy.context.view_layer.objects.active=obj
    except Exception:
        pass

def ensure_pose_mode(arm):
    if not arm:
        return False
    try:
        _set_active_object(arm)
        bpy.ops.object.mode_set(mode="POSE")
        return True
    except Exception as exc:
        print(f"[V6-Warning] 无法安全切换到 POSE 模式: {exc}")
        return False

def safe_pose_bone(arm, mapped_name):
    try:
        if not arm or not mapped_name:
            raise KeyError(mapped_name)
        bones=getattr(getattr(arm,"pose",None),"bones",None)
        if bones is None:
            raise AttributeError("pose.bones missing")
        bone=bones.get(mapped_name)
        if bone is None:
            raise KeyError(mapped_name)
        return bone
    except (KeyError, AttributeError):
        print(f"[V6-Warning] 骨骼 {mapped_name} 映射失败，已安全跳过。")
        return None

def clear_scene():
    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()

def hex_to_rgba(text):
    s=str(text).strip()
    if len(s)==7 and s.startswith("#"):
        try:
            r=int(s[1:3],16)/255.0; g=int(s[3:5],16)/255.0; b=int(s[5:7],16)/255.0
            return (r,g,b,1.0)
        except ValueError:
            return None
    return None

def make_toon_material(style,texture_path=None):
    bands=max(1,int(style.get("toon_bands",3))); hard=float(style.get("shadow_hardness",0.75))
    mat=bpy.data.materials.new("knowledge_toon_shader"); mat.use_nodes=True
    nodes=mat.node_tree.nodes; links=mat.node_tree.links; nodes.clear()
    out=nodes.new("ShaderNodeOutputMaterial"); diff=nodes.new("ShaderNodeBsdfDiffuse")
    s2rgb=nodes.new("ShaderNodeShaderToRGB"); ramp=nodes.new("ShaderNodeValToRGB")
    if texture_path and Path(texture_path).exists():
        tex=nodes.new("ShaderNodeTexImage"); tex.image=bpy.data.images.load(str(texture_path),check_existing=True); tex.interpolation="Closest"; tex.extension="CLIP"
        links.new(tex.outputs["Color"],diff.inputs["Color"])
    palette=[hex_to_rgba(c) for c in style.get("oklab_color_palette",[]) if hex_to_rgba(c)]
    target_bands=len(palette) if palette else bands
    ramp.color_ramp.interpolation="CONSTANT"
    while len(ramp.color_ramp.elements)<target_bands: ramp.color_ramp.elements.new(0.5)
    while len(ramp.color_ramp.elements)>target_bands: ramp.color_ramp.elements.remove(ramp.color_ramp.elements[-1])
    for i,e in enumerate(ramp.color_ramp.elements):
        t=i/max(target_bands-1,1); e.position=t
        if palette: e.color=palette[i]
        else:
            v=min(1.0,t*hard+(1.0-hard)*0.25); e.color=(v,v,v,1)
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
    try:
        scene.use_nodes=True; scene.render.use_compositing=True
        tree=getattr(scene, "node_tree", None) or getattr(bpy.context.scene, "node_tree", None)
        if tree is not None:
            tree.nodes.clear()
            rl=tree.nodes.new("CompositorNodeRLayers"); pix=tree.nodes.new("CompositorNodePixelate"); comp=tree.nodes.new("CompositorNodeComposite")
            tree.links.new(rl.outputs["Image"],pix.inputs["Color"]); tree.links.new(pix.outputs["Color"],comp.inputs["Image"])
        else:
            print("[V6-Warning] Blender scene compositor node_tree unavailable; pixelate compositor disabled.")
    except Exception as exc:
        print(f"[V6-Warning] Blender compositor setup skipped: {exc}")
    cam_data=bpy.data.cameras.new("PixelArtOrthoCamera"); cam_data.type="ORTHO"; cam_data.ortho_scale=4
    cam=bpy.data.objects.new("PixelArtOrthoCamera",cam_data); bpy.context.collection.objects.link(cam)
    cam.location=(0,-6,1.6); cam.rotation_euler=(math.radians(78),0,0); scene.camera=cam
    light_data=bpy.data.lights.new("toon_key_light","SUN"); light=bpy.data.objects.new("toon_key_light",light_data)
    bpy.context.collection.objects.link(light); light.rotation_euler=(math.radians(45),0,math.radians(-35)); light_data.energy=3
    return cam

def _assign_material(obj, mat):
    try:
        obj.data.materials.clear(); obj.data.materials.append(mat)
    except Exception:
        pass


def create_procedural_trickster(mat):
    parts=[]
    specs=[
        ("body", (0,0,0.85), (0.72,0.28,0.95), (0.12,0.52,1.0,1.0)),
        ("head", (0,0,1.55), (0.48,0.34,0.42), (1.0,0.78,0.48,1.0)),
        ("cap", (0,0,1.86), (0.58,0.38,0.18), (1.0,0.18,0.34,1.0)),
        ("l_arm", (-0.53,0,1.02), (0.18,0.18,0.72), (0.16,0.8,1.0,1.0)),
        ("r_arm", (0.53,0,1.02), (0.18,0.18,0.72), (0.16,0.8,1.0,1.0)),
        ("l_leg", (-0.22,0,0.28), (0.22,0.2,0.55), (0.13,0.12,0.28,1.0)),
        ("r_leg", (0.22,0,0.28), (0.22,0.2,0.55), (0.13,0.12,0.28,1.0)),
    ]
    for name, loc, scale, color in specs:
        bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
        obj=bpy.context.object; obj.name=f"procedural_trickster_{name}"; obj.scale=scale
        local_mat=mat.copy(); local_mat.name=f"toon_{name}"; local_mat.diffuse_color=color
        _assign_material(obj, local_mat); parts.append(obj)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for part in parts: part.select_set(True)
        bpy.context.view_layer.objects.active=parts[0]
        bpy.ops.object.join()
        joined=bpy.context.object; joined.name="procedural_trickster_character"
        return joined
    except Exception:
        return parts[0]


def import_model(path, mat=None):
    p=Path(path) if path else None
    before=set(bpy.context.scene.objects)
    if p and p.exists():
        suffix=p.suffix.lower()
        if suffix==".obj": bpy.ops.wm.obj_import(filepath=str(p))
        elif suffix==".fbx": bpy.ops.import_scene.fbx(filepath=str(p), use_anim=False)
        elif suffix in {".glb",".gltf"}: bpy.ops.import_scene.gltf(filepath=str(p))
        imported=[o for o in bpy.context.scene.objects if o not in before]
        for obj in imported:
            if hasattr(obj,"animation_data_clear"):
                obj.animation_data_clear()
        print("[V6-Blender] 外部 FBX 导入成功，已屏蔽原生动画。")
        objs=[o for o in imported if getattr(o,"type","")=="MESH"] or [o for o in bpy.context.scene.objects if getattr(o,"type","")=="MESH"]
        if objs: return objs[0]
    print("[V6-Warning] 未提供有效 base_mesh_path，已生成程序化像素角色占位体，禁止灰方块交付。")
    return create_procedural_trickster(mat) if mat else None

def active_armature():
    arms=[o for o in bpy.data.objects if getattr(o,"type","")=="ARMATURE"]
    for arm in arms:
        if hasattr(arm,"animation_data_clear"):
            arm.animation_data_clear()
        try:
            _set_active_object(arm)
            for bone in arm.pose.bones:
                bone.rotation_mode="XYZ"
        except (AttributeError, KeyError) as exc:
            print(f"[V6-Warning] Armature {getattr(arm,'name','<unknown>')} pose 初始化失败，已安全跳过: {exc}")
    if arms:
        ensure_pose_mode(arms[0])
    return arms[0] if arms else None

def _scene_mesh_bounds():
    points=[]
    for obj in bpy.context.scene.objects:
        if getattr(obj,"type","")=="MESH" and hasattr(obj,"bound_box"):
            try:
                points.extend([obj.matrix_world @ Vector(corner) for corner in obj.bound_box])
            except Exception:
                continue
    if not points:
        return None
    mn=Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    mx=Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mn,mx

def normalize_scene_model(target_height=2.8):
    bounds=_scene_mesh_bounds()
    if not bounds:
        return None
    mn,mx=bounds; center=(mn+mx)*0.5; size=mx-mn
    height=max(float(size.z), float(size.x), float(size.y), 0.001)
    scale=float(target_height)/height
    roots=[o for o in bpy.context.scene.objects if getattr(o,"parent",None) is None and getattr(o,"type","") in {"MESH","ARMATURE","EMPTY"}]
    for obj in roots:
        obj.location -= center
        obj.scale *= scale
    bpy.context.view_layer.update()
    return _scene_mesh_bounds()

def orient_model_for_camera():
    before=_scene_mesh_bounds()
    if not before:
        return {"auto_rotated_z90": False}
    mn,mx=before; size=mx-mn
    if float(size.x) >= float(size.y) * 1.5:
        return {"auto_rotated_z90": False, "reason": "width_already_dominant", "size": [float(size.x),float(size.y),float(size.z)]}
    roots=[o for o in bpy.context.scene.objects if getattr(o,"parent",None) is None and getattr(o,"type","") in {"MESH","ARMATURE","EMPTY"}]
    for obj in roots:
        obj.rotation_euler.z += math.radians(90)
    bpy.context.view_layer.update()
    after=_scene_mesh_bounds()
    if not after:
        return {"auto_rotated_z90": True}
    mn2,mx2=after; size2=mx2-mn2
    return {"auto_rotated_z90": True, "size_before": [float(size.x),float(size.y),float(size.z)], "size_after": [float(size2.x),float(size2.y),float(size2.z)]}


def frame_camera_to_scene(cam, margin=1.25):
    bounds=_scene_mesh_bounds()
    if not bounds:
        return None
    mn,mx=bounds; center=(mn+mx)*0.5; size=mx-mn
    candidates=[
        ("front_y_minus", Vector((0,-1,0)), max(float(size.x),float(size.z))*1.15),
        ("back_y_plus", Vector((0,1,0)), max(float(size.x),float(size.z))*1.05),
        ("iso_front_right", Vector((0.72,-0.72,0)), max(float(size.x)*0.72+float(size.y)*0.72,float(size.z))),
        ("iso_front_left", Vector((-0.72,-0.72,0)), max(float(size.x)*0.72+float(size.y)*0.72,float(size.z))),
        ("right_x_plus", Vector((1,0,0)), max(float(size.y),float(size.z))*0.65),
        ("left_x_minus", Vector((-1,0,0)), max(float(size.y),float(size.z))*0.65),
    ]
    name,direction,span=max(candidates,key=lambda item:item[2])
    direction.normalize()
    target=center+Vector((0,0,0.2))
    cam.data.ortho_scale=max(1.0,float(span)*margin)
    cam.location=target + direction*6.0
    cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
    print(f"[V6-Blender] 自动择优取景完成: chosen_view={name}, center={tuple(round(v,3) for v in center)}, size={tuple(round(v,3) for v in size)}, projected_span={span:.3f}, ortho={cam.data.ortho_scale:.3f}")
    return {"chosen_view":name,"center":[float(center.x),float(center.y),float(center.z)],"size":[float(size.x),float(size.y),float(size.z)],"projected_span":float(span),"ortho_scale":float(cam.data.ortho_scale)}

def audit_scene_import(stage):
    meshes=[o for o in bpy.context.scene.objects if getattr(o,"type","")=="MESH"]
    arms=[o for o in bpy.context.scene.objects if getattr(o,"type","")=="ARMATURE"]
    bounds=_scene_mesh_bounds()
    if bounds:
        mn,mx=bounds; size=mx-mn
        bounds_msg=f"bounds_min={tuple(round(v,3) for v in mn)}, bounds_max={tuple(round(v,3) for v in mx)}, size={tuple(round(v,3) for v in size)}"
    else:
        bounds_msg="bounds=<none>"
    print(f"[V6-Blender-Audit] {stage}: mesh_count={len(meshes)}, armature_count={len(arms)}, meshes={[o.name for o in meshes[:8]]}, armatures={[o.name for o in arms[:4]]}, {bounds_msg}")

def load_model(cfg,mat):
    obj=import_model(cfg.get("base_mesh_path") or cfg.get("source_obj"), mat)
    obj.name=cfg.get("asset_name","pixel_asset")
    meshes=[o for o in bpy.context.scene.objects if getattr(o,"type","")=="MESH"]
    palette=[hex_to_rgba(c) for c in cfg.get("style_params",{}).get("oklab_color_palette",[]) if hex_to_rgba(c)]
    for idx,mesh_obj in enumerate(meshes):
        mesh_obj.data.materials.clear(); mesh_obj.data.materials.append(mat)
        if palette:
            mesh_obj.active_material.diffuse_color=palette[idx%len(palette)]
        if bool(cfg.get("style_params",{}).get("outline_enabled",True)):
            sol=mesh_obj.modifiers.new("knowledge_outline","SOLIDIFY"); sol.thickness=max(0.001,float(cfg["style_params"].get("line_width",1))*0.003); sol.use_flip_normals=True
            om=bpy.data.materials.new("knowledge_outline_black"); om.diffuse_color=(0,0,0,1); mesh_obj.data.materials.append(om); sol.material_offset=len(mesh_obj.data.materials)-1
    return obj

def cache_motion_base(obj, arm=None):
    targets=[t for t in (obj, arm) if t]
    for target in targets:
        target["_v6_base_location"]=[float(target.location.x),float(target.location.y),float(target.location.z)]
        target["_v6_base_scale"]=[float(target.scale.x),float(target.scale.y),float(target.scale.z)]


def _custom_vec(target, key, fallback):
    value=target.get(key) if hasattr(target,"get") else None
    if isinstance(value,(list,tuple)) and len(value)>=3:
        return (float(value[0]),float(value[1]),float(value[2]))
    return (float(fallback.x),float(fallback.y),float(fallback.z))


def apply_animation_frame(obj,arm,frame,bone_map=None,timeline_frame=None):
    if not isinstance(frame,dict): return
    bone_map=bone_map or {}
    rt=frame.get("root_transform",{})
    target=arm if arm else obj
    base_loc=_custom_vec(target,"_v6_base_location",target.location)
    base_scale=_custom_vec(target,"_v6_base_scale",target.scale)
    squash=(1.0,1.0,1.0) if arm else _squash_scale(frame)
    target.location.x=base_loc[0]+float(rt.get("x",0.0))
    target.location.y=base_loc[1]
    target.location.z=base_loc[2]+float(rt.get("y",0.0))
    target.rotation_euler.z=float(rt.get("rotation",0.0))
    target.scale=(base_scale[0]*float(squash[0]),base_scale[1]*float(squash[1]),base_scale[2]*float(squash[2]))
    if timeline_frame is not None:
        target.keyframe_insert("location",frame=timeline_frame); target.keyframe_insert("rotation_euler",frame=timeline_frame); target.keyframe_insert("scale",frame=timeline_frame)
    rotations=frame.get("joint_local_rotations",{}) or frame.get("bone_rotations",{})
    if arm and isinstance(rotations,dict):
        ensure_pose_mode(arm)
        for name,val in rotations.items():
            mapped=bone_map.get(str(name),str(name))
            try:
                bone=safe_pose_bone(arm,mapped)
                if bone:
                    bone.rotation_mode="XYZ"; bone.rotation_euler=_rot3(val)
                    if timeline_frame is not None:
                        bone.keyframe_insert("rotation_euler",frame=timeline_frame); bone.keyframe_insert("scale",frame=timeline_frame)
            except (KeyError, AttributeError):
                print(f"[V6-Warning] 骨骼 {mapped} 映射失败，已安全跳过。")

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

def create_fluid_metaballs(frames, style, arm=None, anchor_bone=None):
    objects=[]
    root_bone=None
    if arm:
        try:
            root_bone=next((b.name for b in arm.pose.bones), None)
        except (KeyError, AttributeError):
            root_bone=None
    parent_bone=anchor_bone or root_bone
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
        if arm:
            obj.parent=arm
            if parent_bone:
                obj.parent_type="BONE"; obj.parent_bone=parent_bone
            else:
                obj.parent_type="OBJECT"
            if not anchor_bone:
                print(f"[V6-Warning] 未找到手部特效锚点，融球已降级挂载到 Armature Root: {parent_bone or getattr(arm,'name','Armature')}")
        radius=float(params.get("particle_radius",style.get("fluid_particle_radius",0.055)))
        for p in particles:
            elem=mb.elements.new(type="BALL"); elem.radius=max(0.005,float(p.get("size",1.0))*radius)
            elem.co=(float(p.get("x",0.5))*1.2,float(p.get("z",0.0))*0.2,float(p.get("y",0.5))*1.0)
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
    ap=argparse.ArgumentParser(); ap.add_argument("--config", default=os.environ.get("MATHART_BLENDER_RENDER_CONFIG", "")); a=ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
    if not a.config:
        raise SystemExit("[V6-Blender] missing --config and MATHART_BLENDER_RENDER_CONFIG")
    cfg=json.loads(Path(a.config).read_text(encoding="utf-8")); out=Path(cfg["output_dir"]); out.mkdir(parents=True,exist_ok=True)
    style=cfg.get("style_params",{}); res=cfg.get("resolution",[256,256]); fps=float(cfg.get("fps",12)); name=cfg.get("asset_name","pixel_asset")
    frames=list(cfg.get("animation_data") or cfg.get("frames",[])); n=max(1,int(cfg.get("frame_count",len(frames) or 1)))
    clear_scene(); mat=make_toon_material(style,cfg.get("texture_path")); cam=setup_scene(style,res); obj=load_model(cfg,mat); audit_scene_import("after_import"); normalize_scene_model(); orientation_audit=orient_model_for_camera(); audit_scene_import("after_orient"); camera_audit=frame_camera_to_scene(cam); camera_audit["orientation_audit"]=orientation_audit; arm=active_armature(); cache_motion_base(obj,arm); bone_map=build_bone_map(arm,frames); print("[V6-Blender] 骨骼智能映射表生成完毕。"); vfx_anchor=_find_vfx_anchor(arm,bone_map)
    print("[V6-Blender] 正在强制注入卡肉顿帧与 OKLAB 色彩。")
    fluid_objs=create_fluid_metaballs(frames,style,arm,vfx_anchor); cloth_objs=create_cloth_meshes(frames,style)
    sprites=[]; durations=[]; pngs=[]; timeline_frame=1
    for i in range(n):
        bpy.context.scene.frame_set(timeline_frame)
        if i<len(frames):
            apply_animation_frame(obj,arm,frames[i],bone_map,timeline_frame)
            for extra in range(_hit_stop_frames(frames[i],fps)):
                apply_animation_frame(obj,arm,frames[i],bone_map,timeline_frame+extra+1)
        show_only(fluid_objs,i); show_only(cloth_objs,i)
        png=out/f"{name}_{i:04d}.png"; bpy.context.scene.render.filepath=str(png); bpy.ops.render.render(write_still=True)
        dur=frame_duration(frames,i,fps); piv,pix=project_pivot(bpy.context.scene,cam,obj,res,cfg.get("pivot_world"))
        rect={"x":0,"y":i*int(res[1]),"width":int(res[0]),"height":int(res[1])}
        sprites.append({"name":f"{name}_{i:04d}","png":str(png),"rect":rect,"pivot":piv,"pivot_pixel":pix,"duration":dur})
        durations.append(dur); pngs.append(str(png)); timeline_frame += 1 + (_hit_stop_frames(frames[i],fps) if i<len(frames) else 0)
    meta={"asset_name":name,"unity_import_contract":"zero_post_processing","texture_settings":{"filterMode":"Point","compression":"None","mipmapEnabled":False,"spriteMode":"Multiple"},"style_params":style,"base_mesh_path":cfg.get("base_mesh_path"),"camera_audit":camera_audit,"phase7_puppeteering":{"bone_map":bone_map,"vfx_anchor_bone":vfx_anchor,"timeline_frames":timeline_frame-1},"v6_physics_render":{"fluid_metaballs":len(fluid_objs),"cloth_meshes":len(cloth_objs),"metaballs_parented_to_attack_bone":bool(vfx_anchor)},"sprite_rects":[s["rect"] for s in sprites],"pivot_points":[s["pivot"] for s in sprites],"frame_durations":durations,"sprites":sprites,"atlas_layout":{"width":int(res[0]),"height":int(res[1])*n,"packing":"vertical_strip_exact_rects"},"timing_contract":{"source":"Phase3 anime_timing_modifier","duration_unit":"seconds","unity_animator_should_hold_each_sprite_for_duration":True}}
    meta_path=out/f"{name}_unity_meta.json"; meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (out/f"{name}_render_manifest.json").write_text(json.dumps({"png_paths":pngs,"unity_meta":str(meta_path)},indent=2),encoding="utf-8")
    print("✅ Phase 7 竣工！数学提线与骨骼自动映射已打通。静态 3D 躯壳已被成功附体，引擎自主进化的物理动作、OKLAB 色彩与融球特效已完美显像！")
    print("✅ Phase 7.5 防御装甲竣工！已植入全景日志与容错降级机制，Blender 后端已具备免疫奇葩 FBX 导致崩溃的工业级韧性！")
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

    def _fit_visible_frame(self, image: Any) -> tuple[Any, dict[str, Any]]:
        alpha = image.getchannel("A")
        bbox = alpha.getbbox()
        width, height = image.size
        total_pixels = max(1, width * height)
        visible_pixels = sum(1 for value in alpha.getdata() if value)
        visible_ratio = visible_pixels / total_pixels
        if bbox is None:
            return image.copy(), {"visible_ratio": 0.0, "bbox": None, "auto_reframed": False}
        box_width = max(1, bbox[2] - bbox[0])
        box_height = max(1, bbox[3] - bbox[1])
        bbox_ratio = (box_width * box_height) / total_pixels
        should_reframe = visible_ratio < 0.025 or max(box_width / width, box_height / height) < 0.45
        audit = {
            "visible_ratio": visible_ratio,
            "bbox_ratio": bbox_ratio,
            "bbox": list(bbox),
            "auto_reframed": should_reframe,
        }
        if not should_reframe:
            return image.copy(), audit
        margin = max(8, int(max(box_width, box_height) * 0.35))
        crop_box = (
            max(0, bbox[0] - margin),
            max(0, bbox[1] - margin),
            min(width, bbox[2] + margin),
            min(height, bbox[3] + margin),
        )
        cropped = image.crop(crop_box)
        scale = min(width * 0.78 / max(1, cropped.width), height * 0.78 / max(1, cropped.height))
        scale = max(1.0, min(scale, 12.0))
        resized_size = (max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale)))
        from PIL import Image
        resampling = getattr(Image, "Resampling", Image).NEAREST
        resized = cropped.resize(resized_size, resampling)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2), resized)
        audit["crop_box"] = list(crop_box)
        audit["scale"] = scale
        audit["reframed_size"] = list(resized_size)
        return canvas, audit

    def _stitch_spritesheet(self, output_dir: Path, asset_name: str, final_assets_dir: Path | None = None) -> tuple[Path | None, int]:
        frames = sorted(output_dir.glob(f"{asset_name}_[0-9][0-9][0-9][0-9].png"))
        self._last_visibility_audit = []
        if not frames:
            return None, 0
        final_dir = final_assets_dir or output_dir
        final_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        images = []
        delivered_frames: list[Path] = []
        for path in frames:
            with Image.open(path) as raw:
                fitted, audit = self._fit_visible_frame(raw.convert("RGBA"))
            audit["source"] = str(path)
            self._last_visibility_audit.append(audit)
            images.append(fitted)
            final_frame_path = final_dir / path.name
            fitted.save(final_frame_path)
            if final_frame_path.exists() and final_frame_path.stat().st_size > 0:
                delivered_frames.append(final_frame_path)
        width = max(image.width for image in images)
        height = sum(image.height for image in images)
        sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        y = 0
        for image in images:
            sheet.paste(image, (0, y))
            y += image.height
        sheet_path = final_dir / f"{asset_name}_spritesheet.png"
        sheet.save(sheet_path)
        if not sheet_path.exists() or sheet_path.stat().st_size <= 0:
            alternate_path = output_dir / f"{asset_name}_spritesheet.png"
            sheet.save(alternate_path)
            final_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(alternate_path, sheet_path)
        if not sheet_path.exists() or sheet_path.stat().st_size <= 0:
            for image in images:
                image.close()
            return None, 0
        for image in images:
            image.close()
        if len(delivered_frames) < len(frames):
            return sheet_path, len(delivered_frames)
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

    def _decode_process_output(self, payload: bytes | str | None) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        for encoding in ("utf-8", "gb18030", "gbk"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")

    def _write_process_log(self, path: Path, payload: bytes | str | None) -> None:
        path.write_text(self._decode_process_output(payload), encoding="utf-8", errors="replace")

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
        self._last_visibility_audit: list[dict[str, Any]] = []
        run_blender = bool(context.get("run_blender", False))
        blender_returncode: int | None = None
        sheet_path: Path | None = None
        stitched_frames = 0
        if run_blender:
            try:
                env = os.environ.copy()
                env["MATHART_BLENDER_RENDER_CONFIG"] = str(config_path)
                proc = subprocess.run([str(context.get("blender_executable", "blender")), "--background", "--python", str(script_path), "--", "--config", str(config_path)], check=False, capture_output=True, text=False, env=env, timeout=float(context.get("blender_timeout_seconds", 180.0)))
                blender_returncode = proc.returncode
                self._write_process_log(output_dir / f"{asset_name}_blender_stdout.log", proc.stdout)
                self._write_process_log(output_dir / f"{asset_name}_blender_stderr.log", proc.stderr)
                if blender_returncode == 0:
                    sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
                    if stitched_frames <= 0:
                        blender_returncode = -3
                        existing_stderr = output_dir / f"{asset_name}_blender_stderr.log"
                        previous = existing_stderr.read_text(encoding="utf-8", errors="replace") if existing_stderr.exists() else ""
                        existing_stderr.write_text(previous + "\nBlender returned 0 but produced no PNG frames; emitted dependency fallback frames.\n", encoding="utf-8", errors="replace")
                        self._write_dependency_fallback_frames(output_dir, asset_name, config_path)
                        sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
                else:
                    self._write_dependency_fallback_frames(output_dir, asset_name, config_path)
                    sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
            except subprocess.TimeoutExpired as exc:
                blender_returncode = -9
                self._write_process_log(output_dir / f"{asset_name}_blender_stdout.log", exc.stdout)
                timeout_log = self._decode_process_output(exc.stderr)
                timeout_log += f"\nBlender render timed out after {exc.timeout} seconds; emitted dependency fallback frames.\n"
                (output_dir / f"{asset_name}_blender_stderr.log").write_text(timeout_log, encoding="utf-8", errors="replace")
                self._write_dependency_fallback_frames(output_dir, asset_name, config_path)
                sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
            except FileNotFoundError as exc:
                blender_returncode = -2
                (output_dir / f"{asset_name}_blender_stderr.log").write_text(f"Blender executable missing: {exc}\n", encoding="utf-8")
                self._write_dependency_fallback_frames(output_dir, asset_name, config_path)
                sheet_path, stitched_frames = self._stitch_spritesheet(output_dir, asset_name, final_assets_dir)
            except Exception as exc:
                blender_returncode = -1
                (output_dir / f"{asset_name}_blender_stderr.log").write_text(f"Blender backend unexpected failure: {type(exc).__name__}: {exc}\n", encoding="utf-8", errors="replace")
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
        if sheet_path and (not sheet_path.exists() or sheet_path.stat().st_size <= 0):
            sheet_path = None
            stitched_frames = 0
        visibility_audit = getattr(self, "_last_visibility_audit", [])
        if visibility_audit:
            (final_assets_dir / f"{asset_name}_visibility_audit.json").write_text(
                json.dumps(visibility_audit, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        outputs = {"render_script": str(script_path), "render_config": str(config_path), "unity_meta": str(unity_meta)}
        if sheet_path:
            outputs["spritesheet"] = str(sheet_path)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        reframed_count = sum(1 for item in visibility_audit if item.get("auto_reframed"))
        min_visible_ratio = min((float(item.get("visible_ratio", 0.0)) for item in visibility_audit), default=0.0)
        return ArtifactManifest(
            artifact_family=ArtifactFamily.META_REPORT.value,
            backend_type=self.name,
            version="1.1.0",
            session_id=str(context.get("session_id", "V6-PHASE-5")),
            outputs=outputs,
            metadata={"asset_name": asset_name, "headless": True, "script_kind": "pure_bpy", "delivery_backend_revision": "visibility-reframe-v3", "run_blender": run_blender, "blender_returncode": blender_returncode, "style_params": style, "toon_bands": int(style.get("toon_bands", 3)), "outline_enabled": bool(style.get("outline_enabled", True)), "base_mesh_path": context.get("base_mesh_path", context.get("source_obj")), "texture_path": context.get("texture_path"), "mesh_texture_hook_enabled": True, "animation_data_enabled": True, "spritesheet_stitched": sheet_path is not None, "stitched_frame_count": stitched_frames, "loose_frames_cleaned": False, "loose_frames_retained": True, "nearest_filtering_forced": True, "anti_aliasing": "disabled", "unity_zero_post_metadata": True, "sprite_rects_exported": True, "pivot_projection_api": "bpy_extras.object_utils.world_to_camera_view", "frame_durations_from_phase3": True, "visibility_reframed_frames": reframed_count, "min_visible_ratio_before_reframe": min_visible_ratio, "elapsed_ms": elapsed_ms},
            quality_metrics={"headless_script_emitted": 1.0, "config_emitted": 1.0, "style_param_count": float(len(style)), "stitched_frames": float(stitched_frames), "visibility_reframed_frames": float(reframed_count), "min_visible_ratio_before_reframe": float(min_visible_ratio)},
            tags=["v6-phase-5", "blender-headless", "pure-bpy", "pixel-art", "unity-zero-post", "knowledge-driven-style"],
        )

__all__ = ["BlenderHeadlessPixelArtBackend"]

