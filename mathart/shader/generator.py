"""ShaderCodeGenerator — generates Unity HLSL shader code from knowledge rules."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Optional

from mathart.shader.knowledge import ShaderKnowledgeBase, ShaderPreset


class ShaderCodeGenerator:
    """Generates Unity HLSL shader code fragments and .shadergraph JSON snippets.

    This generator translates shader knowledge rules into actual Unity shader
    code that can be directly used in a Unity project.

    Output formats
    --------------
    hlsl_fragment   : HLSL code fragment for custom shader inclusion
    shadergraph_json: Unity Shader Graph node JSON (importable)
    urp_properties  : Unity shader Properties block
    """

    def __init__(self, knowledge: Optional[ShaderKnowledgeBase] = None) -> None:
        self.knowledge = knowledge or ShaderKnowledgeBase()

    def generate_properties_block(self, shader_type: str) -> str:
        """Generate a Unity shader Properties block for a given shader type."""
        params = self.knowledge.get_params(shader_type)
        if not params:
            return "// No parameters defined for shader type: " + shader_type

        lines = ["Properties", "{"]
        for p in params:
            if p.hlsl_type == "int":
                lines.append(
                    f'    {p.name} ("{p.name[1:]}", Int) = {int(p.default)}'
                )
            elif p.hlsl_type == "float4":
                lines.append(
                    f'    {p.name} ("{p.name[1:]}", Color) = (1,1,1,1)'
                )
            else:
                lines.append(
                    f'    {p.name} ("{p.name[1:]}", Range({p.min_val}, {p.max_val})) '
                    f"= {p.default}"
                )
        lines.append("}")
        return "\n".join(lines)

    def generate_hlsl_fragment(self, shader_type: str, preset_name: Optional[str] = None) -> str:
        """Generate an HLSL fragment for the given shader type.

        Parameters
        ----------
        shader_type : str
            One of: sprite_lit, outline, palette_swap, pseudo_3d_depth
        preset_name : str, optional
            If provided, use preset parameter values as defaults.
        """
        preset = self.knowledge.get_preset(preset_name) if preset_name else None
        params = self.knowledge.get_params(shader_type)

        if shader_type == "sprite_lit":
            return self._gen_sprite_lit(params, preset)
        elif shader_type == "outline":
            return self._gen_outline(params, preset)
        elif shader_type == "palette_swap":
            return self._gen_palette_swap(params, preset)
        elif shader_type == "pseudo_3d_depth":
            return self._gen_pseudo3d(params, preset)
        else:
            return f"// Shader type '{shader_type}' not yet implemented\n"

    def generate_shadergraph_json(
        self,
        shader_type: str,
        preset_name: Optional[str] = None,
    ) -> str:
        """Generate a minimal Unity Shader Graph JSON snippet."""
        preset = self.knowledge.get_preset(preset_name) if preset_name else None
        params = self.knowledge.get_params(shader_type)

        nodes = []
        for i, p in enumerate(params):
            val = preset.params.get(p.name, p.default) if preset else p.default
            nodes.append({
                "type": "UnityEditor.ShaderGraph.Vector1Node",
                "name": p.name,
                "value": val,
                "position": {"x": i * 200, "y": 0},
            })

        graph = {
            "m_SGVersion": 3,
            "m_Type": "UnityEditor.ShaderGraph.GraphData",
            "m_ShaderType": shader_type,
            "m_Nodes": nodes,
            "m_Properties": [
                {
                    "m_Name": p.name,
                    "m_DefaultValue": preset.params.get(p.name, p.default) if preset else p.default,
                    "m_Min": p.min_val,
                    "m_Max": p.max_val,
                }
                for p in params
            ],
        }
        return json.dumps(graph, indent=2)

    def save_all(self, output_dir: Path, shader_type: str, preset_name: Optional[str] = None) -> list[Path]:
        """Save all generated shader files to output_dir."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = []

        # HLSL fragment
        hlsl = self.generate_hlsl_fragment(shader_type, preset_name)
        hlsl_path = output_dir / f"{shader_type}.hlsl"
        hlsl_path.write_text(hlsl, encoding="utf-8")
        saved.append(hlsl_path)

        # Properties block
        props = self.generate_properties_block(shader_type)
        props_path = output_dir / f"{shader_type}_properties.txt"
        props_path.write_text(props, encoding="utf-8")
        saved.append(props_path)

        # Shader Graph JSON
        sg_json = self.generate_shadergraph_json(shader_type, preset_name)
        sg_path = output_dir / f"{shader_type}.shadergraph.json"
        sg_path.write_text(sg_json, encoding="utf-8")
        saved.append(sg_path)

        return saved

    # ── HLSL generators ────────────────────────────────────────────────────────

    def _gen_sprite_lit(self, params, preset) -> str:
        p = {sp.name: (preset.params.get(sp.name, sp.default) if preset else sp.default)
             for sp in params}
        return textwrap.dedent(f"""\
            // MarioTrickster Sprite Lit Shader Fragment
            // Generated by ShaderCodeGenerator v0.3.0
            // Knowledge sources: unity_rules.md, color_light.md

            // --- Properties ---
            float _NormalStrength;    // [{p.get('_NormalStrength', 1.0):.2f}] Normal map influence
            float _AmbientOcclusion;  // [{p.get('_AmbientOcclusion', 0.3):.2f}] AO intensity
            float _ShadowIntensity;   // [{p.get('_ShadowIntensity', 0.6):.2f}] Shadow darkness
            float _RimLightWidth;     // [{p.get('_RimLightWidth', 0.1):.2f}] Rim edge width
            float _RimLightIntensity; // [{p.get('_RimLightIntensity', 1.2):.2f}] Rim brightness
            int   _PixelSnap;         // [{int(p.get('_PixelSnap', 1))}] Pixel-perfect snap

            // --- Vertex ---
            v2f vert(appdata v) {{
                v2f o;
                // Pixel-perfect snapping: round to nearest pixel
                float4 pos = UnityObjectToClipPos(v.vertex);
                if (_PixelSnap) {{
                    pos.xy = round(pos.xy / pos.w * _ScreenParams.xy) /
                             _ScreenParams.xy * pos.w;
                }}
                o.pos = pos;
                o.uv  = v.uv;
                return o;
            }}

            // --- Fragment ---
            fixed4 frag(v2f i) : SV_Target {{
                fixed4 col = tex2D(_MainTex, i.uv);

                // Normal mapping (math: N = normalize(2*n-1) * _NormalStrength)
                float3 normal = UnpackNormal(tex2D(_NormalMap, i.uv));
                normal.xy *= _NormalStrength;
                normal = normalize(normal);

                // Ambient occlusion
                float ao = lerp(1.0, tex2D(_AOMap, i.uv).r, _AmbientOcclusion);

                // Shadow (math: shadow = lerp(col, 0, _ShadowIntensity * shadowMask))
                float shadow = tex2D(_ShadowMap, i.uv).r;
                col.rgb = lerp(col.rgb, col.rgb * (1.0 - _ShadowIntensity), shadow);

                // Rim light (math: rim = pow(1 - dot(N, V), 1/_RimLightWidth))
                float rim = pow(1.0 - saturate(dot(normal, float3(0,0,1))),
                               1.0 / max(_RimLightWidth, 0.001));
                col.rgb += rim * _RimLightIntensity * fixed3(1,1,1);

                col.rgb *= ao;
                return col;
            }}
        """)

    def _gen_outline(self, params, preset) -> str:
        p = {sp.name: (preset.params.get(sp.name, sp.default) if preset else sp.default)
             for sp in params}
        return textwrap.dedent(f"""\
            // MarioTrickster Outline Shader Fragment
            // Generated by ShaderCodeGenerator v0.3.0
            // Knowledge sources: unity_rules.md (1px crisp outline rule)

            float _OutlineWidth;     // [{p.get('_OutlineWidth', 1.0):.1f}px] Outline width
            float _OutlineAlpha;     // [{p.get('_OutlineAlpha', 1.0):.2f}] Outline opacity
            float _OutlineDepthBias; // [{p.get('_OutlineDepthBias', 0.0):.2f}] Depth bias
            float4 _OutlineColor;    // Outline color (set in material)

            // Sobel edge detection kernel
            // Math: edge = sqrt(Gx^2 + Gy^2) where Gx,Gy are Sobel gradients
            float SobelEdge(sampler2D tex, float2 uv, float2 texelSize) {{
                float2 d = texelSize * _OutlineWidth;
                float tl = tex2D(tex, uv + float2(-d.x,  d.y)).a;
                float tm = tex2D(tex, uv + float2(   0,  d.y)).a;
                float tr = tex2D(tex, uv + float2( d.x,  d.y)).a;
                float ml = tex2D(tex, uv + float2(-d.x,    0)).a;
                float mr = tex2D(tex, uv + float2( d.x,    0)).a;
                float bl = tex2D(tex, uv + float2(-d.x, -d.y)).a;
                float bm = tex2D(tex, uv + float2(   0, -d.y)).a;
                float br = tex2D(tex, uv + float2( d.x, -d.y)).a;
                float gx = -tl - 2*ml - bl + tr + 2*mr + br;
                float gy = -tl - 2*tm - tr + bl + 2*bm + br;
                return sqrt(gx*gx + gy*gy);
            }}

            fixed4 frag(v2f i) : SV_Target {{
                fixed4 col = tex2D(_MainTex, i.uv);
                float edge = SobelEdge(_MainTex, i.uv, _MainTex_TexelSize.xy);
                float outlineMask = step(0.1, edge);
                fixed4 outlineCol = _OutlineColor;
                outlineCol.a *= _OutlineAlpha;
                return lerp(col, outlineCol, outlineMask * (1.0 - col.a));
            }}
        """)

    def _gen_palette_swap(self, params, preset) -> str:
        p = {sp.name: (preset.params.get(sp.name, sp.default) if preset else sp.default)
             for sp in params}
        return textwrap.dedent(f"""\
            // MarioTrickster Palette Swap Shader Fragment
            // Generated by ShaderCodeGenerator v0.3.0
            // Knowledge sources: color_science.md (OKLAB palette, Floyd-Steinberg)

            int   _PaletteSize;      // [{int(p.get('_PaletteSize', 8))}] Number of palette colors
            float _ColorTolerance;   // [{p.get('_ColorTolerance', 0.05):.3f}] Match tolerance
            float _DitherStrength;   // [{p.get('_DitherStrength', 0.0):.2f}] Dither amount
            sampler2D _PaletteTex;   // 1D LUT texture (1xN RGBA)

            // Find nearest palette color using RGB distance
            // Math: d = sqrt((r1-r2)^2 + (g1-g2)^2 + (b1-b2)^2)
            fixed4 NearestPaletteColor(fixed4 col) {{
                fixed4 best = tex2D(_PaletteTex, float2(0.5/_PaletteSize, 0.5));
                float bestDist = 999.0;
                for (int k = 0; k < _PaletteSize; k++) {{
                    float u = (k + 0.5) / _PaletteSize;
                    fixed4 pc = tex2D(_PaletteTex, float2(u, 0.5));
                    float3 diff = col.rgb - pc.rgb;
                    float dist = dot(diff, diff);  // squared RGB distance
                    if (dist < bestDist) {{
                        bestDist = dist;
                        best = pc;
                    }}
                }}
                return best;
            }}

            // Bayer 4x4 ordered dithering matrix
            // Math: threshold = bayer[x%4][y%4] / 16.0
            float BayerDither(float2 pos) {{
                int2 p = int2(fmod(pos, 4));
                float bayer[16] = {{
                     0,  8,  2, 10,
                    12,  4, 14,  6,
                     3, 11,  1,  9,
                    15,  7, 13,  5
                }};
                return bayer[p.y * 4 + p.x] / 16.0 - 0.5;
            }}

            fixed4 frag(v2f i) : SV_Target {{
                fixed4 col = tex2D(_MainTex, i.uv);
                // Apply dithering before quantization
                col.rgb += BayerDither(i.uv * _ScreenParams.xy) * _DitherStrength * 0.1;
                col.rgb = saturate(col.rgb);
                // Palette quantization
                fixed4 quantized = NearestPaletteColor(col);
                return fixed4(quantized.rgb, col.a);
            }}
        """)

    def _gen_pseudo3d(self, params, preset) -> str:
        p = {sp.name: (preset.params.get(sp.name, sp.default) if preset else sp.default)
             for sp in params}
        return textwrap.dedent(f"""\
            // MarioTrickster Pseudo-3D Depth Shader Fragment
            // Generated by ShaderCodeGenerator v0.3.0
            // Status: EXPERIMENTAL — requires future upgrade
            // Knowledge sources: differentiable_rendering.md

            float _DepthScale;       // [{p.get('_DepthScale', 0.5):.2f}] Vertical depth offset
            int   _ParallaxLayers;   // [{int(p.get('_ParallaxLayers', 3))}] Parallax layer count
            float _IsometricAngle;   // [{p.get('_IsometricAngle', 30.0):.1f}°] Isometric angle
            float _DepthFogStart;    // [{p.get('_DepthFogStart', 5.0):.1f}] Fog start distance
            float _NormalMapDepth;   // [{p.get('_NormalMapDepth', 0.8):.2f}] Normal depth scale

            // Isometric projection math:
            // x_iso = (x - z) * cos(angle)
            // y_iso = (x + z) * sin(angle) - y
            float2 IsometricProject(float3 worldPos) {{
                float rad = radians(_IsometricAngle);
                float x_iso = (worldPos.x - worldPos.z) * cos(rad);
                float y_iso = (worldPos.x + worldPos.z) * sin(rad) - worldPos.y;
                return float2(x_iso, y_iso);
            }}

            // Parallax offset: UV shift based on depth layer and view angle
            // Math: uv_offset = depth * tan(viewAngle) * layerScale
            float2 ParallaxOffset(float depth, float2 viewDir, int layer) {{
                float layerScale = 1.0 / _ParallaxLayers;
                return viewDir * depth * _DepthScale * layerScale * layer;
            }}

            // Depth fog: linear fog based on world-space depth
            // Math: fog = saturate((depth - fogStart) / fogRange)
            float DepthFog(float depth) {{
                return saturate((depth - _DepthFogStart) / 10.0);
            }}

            // NOTE: Full pseudo-3D rendering requires:
            // 1. Depth texture from camera (_CameraDepthTexture)
            // 2. Normal map generated from 2D sprite
            // 3. Multi-pass rendering for parallax layers
            // This fragment shows the math; full implementation requires Unity setup.
            fixed4 frag(v2f i) : SV_Target {{
                fixed4 col = tex2D(_MainTex, i.uv);

                // Sample normal map for pseudo-3D lighting
                float3 normal = UnpackNormal(tex2D(_NormalMap, i.uv));
                normal.xy *= _NormalMapDepth;
                normal = normalize(normal);

                // Depth-based fog
                float depth = tex2D(_CameraDepthTexture, i.uv).r;
                float fog = DepthFog(LinearEyeDepth(depth));
                col.rgb = lerp(col.rgb, float3(0.5, 0.6, 0.8), fog * 0.3);

                return col;
            }}
        """)
