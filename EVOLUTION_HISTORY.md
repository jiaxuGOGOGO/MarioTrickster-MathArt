# Evolution History — MarioTrickster-MathArt

This file records every significant improvement to the project.


## SESSION-005 — v0.6.0 (2026-04-14)
- **Best score**: 0.000
- **Tests**: 371
- Added SpriteAnalyzer: extract style fingerprints from reference sprites
- Added SpriteSheetParser: auto-cut spritesheets into frames
- Added SpriteLibrary: persistent, dedup-aware sprite knowledge store
- Added ArtMathQualityController: 4-checkpoint quality control across full pipeline
- Added ProjectMemory: cross-session persistent brain (PROJECT_BRAIN.json)
- Added SessionHandoff: seamless context continuity across conversations
- Added LevelSpecBridge: translate level requirements to asset specs
- Added StagnationGuard: invalid iteration detection + AI arbitration
- Added DeduplicationEngine: knowledge dedup with semantic similarity
- Added Unity Shader knowledge module + Pseudo3D extension skeleton
- Added MathPaperMiner: automated math model paper discovery
- Added 6 knowledge files: PCG, PBR, SDF, color science, procedural animation, differentiable rendering
- Total: 371 tests passing, 0 failures

> Major architecture upgrade: self-evolving brain system fully operational
