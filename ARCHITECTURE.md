# Architecture — BlazEng AI Production Studio

> How the system is designed. For contributors and deep dives.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          User                                    │
│                    (Qt6 Desktop App)                             │
└────────────────────────┬────────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
      [Timeline]                    [Asset Browser]
          │                             │
          └──────────────┬──────────────┘
                         │
                  [Main Window]
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
    [Director]       [Orchestrator]   [WorldState]
       │                 │                 │
  Story Planning    Task Execution   Character Memory
       │                 │                 │
       └─────────────────┼─────────────────┘
                    Pipeline Core
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
   [Asset Manager]  [Scene Composer]  [Validators]
       │                 │                 │
   Art Generation   3D Assembly       Consistency
       │                 │                 │
       └─────────────────┼─────────────────┘
                    Pipeline Processing
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
    [Godot Render]   [Repair Engine]  [FFmpeg Export]
       │                 │                 │
   Frame Output     Error Correction   Video Assembly
       │                 │                 │
       └─────────────────┴─────────────────┘
                    External Tools
```

---

## Core Modules

### 1. Director

**Purpose**: Parse user prompts into a structured shot plan.

**Responsibilities**:
- Break narrative into individual shots with metadata
- Assign camera angles, lighting, character positioning
- Create task schedule for pipeline execution
- Repair prompts when validation fails

**Interface**:
```python
class Director:
    def __init__(self, text_provider, world_state):
        """text_provider: LLM for planning
           world_state: continuity reference"""
    
    def generate_production_plan(self, prompt: str) -> ProductionPlan:
        """Parse prompt → structured shot plan"""
        # Returns plan with 10-20 shots, each with:
        #   - shot_id, scene_description, characters, camera_angle,
        #   - lighting, action, expected_duration
    
    def create_task_schedule(self, plan: ProductionPlan) -> List[Task]:
        """Plan → task queue for orchestrator"""
        # Returns tasks in dependency order:
        # 1. Generate character assets (parallel)
        # 2. Generate environment assets (parallel)
        # 3. Compose scenes (sequential, depends on assets)
        # 4. Render (sequential)
        # 5. Validate (parallel)
        # 6. Repair if needed (sequential)
    
    def repair_shot(self, shot_id: str, feedback: str) -> None:
        """Re-prompt a specific shot based on validation feedback"""
```

**Data Model**:
```python
@dataclass
class Shot:
    shot_id: str
    scene_description: str
    characters: List[str]  # references to world_state
    camera_angle: str      # "wide", "close-up", "overhead", etc.
    lighting: str          # "bright daylight", "harsh shadows", etc.
    action: str            # what happens in this shot
    duration_seconds: float
    assets_needed: List[str]  # character names, props, locations

@dataclass
class ProductionPlan:
    prompt: str
    shots: List[Shot]
    world_state_seed: Dict  # initial character descriptions, world rules
```

### 2. WorldStateManager

**Purpose**: Remember character appearance, injuries, props, and events across the entire video.

**Responsibilities**:
- Store and retrieve character state (appearance, clothing, injuries)
- Track object and prop state
- Record world events for continuity
- Validate character consistency across shots

**Interface**:
```python
class WorldStateManager:
    def __init__(self, config: Dict):
        """Initialize SQLite database for state"""
    
    def add_character(self, name: str, metadata: Dict) -> None:
        """Register character with initial appearance"""
        # metadata: {"appearance": "...", "clothing": "...", "height": "6ft", ...}
    
    def get_character(self, name: str) -> Optional[Dict]:
        """Retrieve current character state"""
    
    def update_character(self, name: str, updates: Dict) -> None:
        """Apply changes (e.g., character gets injured)"""
    
    def add_shot(self, shot_id: str, metadata: Dict) -> None:
        """Record shot and its character states"""
    
    def validate_continuity(self, shot_id: str, rendered_frame: Path) -> bool:
        """Check if rendered frame matches expected state"""
        # Uses vision validator to compare character appearance, props, etc.
```

**Schema** (SQLite):
```
characters
  id, name, first_appearance_shot, appearance_description, version

character_states
  id, character_id, shot_id, clothing, injuries, props, version_timestamp

world_events
  id, shot_id, event_description, timestamp

shots
  id, shot_id, world_state_id (json blob of all character states at this point)
```

### 3. AssetManager

**Purpose**: Generate artwork for characters, objects, environments; cache and version everything.

**Responsibilities**:
- Call image generation provider (Gemini, Stable Diffusion) for artworks
- Cache and deduplicate (same character → reuse image)
- Track asset versions (john/v1, john/v2 if character changes)
- Return consistent paths for downstream use

**Interface**:
```python
class AssetManager:
    def __init__(self, config: Dict, image_provider):
        """image_provider: Imagen, Stable Diffusion, etc."""
    
    def get_asset(self, asset_type: str, name: str, 
                  description: str, force_regenerate=False) -> Path:
        """Generate or retrieve cached asset
        
        asset_type: "character", "object", "environment"
        name: "john", "warehouse_key", "1940s_warehouse"
        
        Logic:
          1. Check if asset exists in cache → return path
          2. If exists but force_regenerate → delete and regenerate
          3. Otherwise: call image_provider.generate()
          4. Save to storage/assets/{asset_type}/{name}/v{N}/{timestamp}.png
          5. Update world_state with asset version
          6. Return path
        """
    
    def list_asset_versions(self, asset_type: str, name: str) -> List[Path]:
        """Show all versions of an asset (for debugging/comparison)"""
```

**Directory Structure**:
```
storage/assets/
├── characters/
│   ├── john/
│   │   ├── v1/
│   │   │   ├── reference.txt  (prompt used)
│   │   │   └── john_v1.png
│   │   └── v2/  (after he gets injured)
│   │       ├── reference.txt
│   │       └── john_v2.png
│   └── jane/
├── objects/
│   ├── warehouse_key/
│   │   └── v1/
└── environments/
    ├── 1940s_warehouse/
        └── v1/
```

### 4. SceneComposer — ✅ Implemented (Aug 2026)

**Purpose**: Build Godot scene files from a Shot and its asset images.

**Status**: Real implementation in `src/core/scene_composer/scene_composer.py`,
100% test coverage (`tests/test_scene_composer.py`), and independently
verified against a real Godot 4.7.2 binary — see "Godot rendering notes"
below for two design decisions this verification actually changed from
what a first-pass reading of Godot's docs would suggest.

**What it does**:
- Writes one shared `project.godot` for the whole production (not one
  project per shot) — reused across every shot's render.
- For each shot, generates a `.tscn` scene file containing a GDScript
  that loads each character's asset image **at runtime** and creates a
  positioned `Sprite2D` for it.
- Character layout: horizontal spread across the viewport in the order
  `shot.characters` lists them; `camera_angle` scales sprite size (wide
  shot = 0.5x, medium = 1.0x, close-up = 1.8x, overhead = 0.7x);
  `lighting` keywords ("dim", "dark", "bright", "harsh", etc.) apply a
  brightness `modulate` to the whole scene as a stand-in for real
  lighting.
- Raises `SceneComposerError` if a shot references a character with no
  corresponding entry in the `assets` dict passed in.

**Interface** (as actually implemented):
```python
class SceneComposer:
    def __init__(self, config: Dict[str, Any]):
        """Creates storage_path/godot_project/ with a shared project.godot
        if one doesn't already exist."""

    def compose_shot(self, shot: Shot, assets: Dict[str, Path]) -> Path:
        """shot: a Director Shot (scene_description, characters,
        camera_angle, lighting, ...). assets: character name -> image
        Path (from AssetManager). Returns the path to the generated
        .tscn file. Raises SceneComposerError if a character has no
        asset."""
```

**Known simplification**: character layout is a simple left-to-right
horizontal spread, not real depth/composition logic, and "lighting" is a
flat color modulate rather than actual 2D/3D lights. Both are reasonable
starting points, not final design — see ROADMAP.md Phase 2 for what's
still open here (props, backgrounds, depth layering).

#### Godot rendering notes (verified against a real binary, Aug 2026)

Two things a first pass at Godot's CLI docs would get wrong, found by
actually running Godot 4.7.2 headlessly during development — not by
reading forum posts:

1. **`--headless` alone cannot render real frame content.** It forces
   Godot's "dummy" display/rendering driver, which has no rasterization
   pipeline at all. Asking it to `--write-movie` a scene with an actual
   textured sprite segfaults (`texture_2d_get`: Parameter "t" is null —
   the dummy driver has no real texture to hand back). **The working
   setup is a virtual X display (`xvfb-run`) plus a real software
   renderer (`--rendering-driver opengl3`, which runs on CPU via Mesa's
   llvmpipe when there's no GPU)** — this genuinely renders and writes
   correct frames, verified by inspecting actual output pixels.

2. **A freshly-generated PNG cannot be loaded as a static `ExtResource`
   in a `.tscn` file.** Godot's resource loader expects an `.import`
   sidecar file (normally created by opening a project in the editor),
   and refuses `res://some_generated.png` with "No loader found for
   resource" if that sidecar doesn't exist — which it never will for an
   AI-generated image that's never touched the Godot editor. **The
   working approach is loading images at *runtime* via GDScript**
   (`Image.new(); img.load(path); ImageTexture.create_from_image(img)`),
   which bypasses the import pipeline entirely. This is what
   `SceneComposer` actually generates.

Also verified: a scene file passed as a positional CLI argument
(`godot --path <project_dir> <scene>.tscn`) overrides that project's
`run/main_scene` for just that invocation — confirmed by rendering two
different scenes from one shared project and getting different pixel
output for each. This is why one shared project works for every shot
instead of needing a project-per-shot.

These findings are encoded directly in `GodotRenderer`
(`src/integrations/godot/renderer.py`) and `SceneComposer`, and covered
by both mocked unit tests (run in normal CI) and a real integration test
against an actual Godot binary (`tests/test_godot_renderer.py`,
`tests/test_orchestrator.py` — gated behind `BLAZENG_GODOT_BINARY` since
CI doesn't have Godot installed by default; see the `godot-integration`
CI job in `.github/workflows/tests.yml`, which downloads a real binary
and runs on pushes to main).

### 5. ValidatorManager

**Purpose**: Check output quality across four independent validators.

**Four Validators**:

#### 5a. ScriptValidator
- Does the rendered frame match the written description?
- Uses text model: "I see [description from script]. Here's the frame. Does it match?"
- Returns: True/False + confidence + feedback

#### 5b. VisualValidator
- Is the visual quality acceptable? (no artifacts, blurriness, etc.)
- Uses vision model: "Rate this frame's quality 1-10"
- Returns: Score + feedback

#### 5c. CharacterValidator
- Are characters dressed/positioned correctly?
- Uses world_state + vision model
- Compares frame to expected appearance from world_state
- Returns: Consistency check + list of discrepancies

#### 5d. TemporalValidator
- Does this shot connect smoothly to the previous one?
- Checks: character position, location continuity, lighting consistency
- Returns: True/False + specific issues

**Interface**:
```python
class ValidatorManager:
    def __init__(self, text_provider, vision_provider):
        pass
    
    def validate_frame(self, frame: Path, shot: Shot, 
                       world_state: WorldState) -> ValidationResult:
        """Run all 4 validators
        
        Returns:
            passed: bool
            results: List[ValidationResult]
              - {validator: "ScriptValidator", passed: bool, feedback: str, confidence: 0.95}
              - {validator: "VisualValidator", passed: bool, score: 8, feedback: "..."}
              - {validator: "CharacterValidator", passed: bool, issues: [...]}
              - {validator: "TemporalValidator", passed: bool, issues: [...]}
        """
```

### 6. RepairEngine

**Purpose**: Fix shots that failed validation without regenerating the entire pipeline.

**Repair Strategy**:
- If script doesn't match: regenerate asset/recompose scene
- If visual quality low: re-render with different settings
- If character wrong: update asset version and re-render
- If temporal mismatch: adjust shot boundary or lighting

**Interface**:
```python
class RepairEngine:
    def __init__(self, asset_manager, scene_composer, director, world_state):
        pass
    
    def repair_shot(self, shot_id: str, failures: List[ValidationResult],
                    attempt: int) -> bool:
        """Fix a failed shot
        
        failures: List of validation failures
        attempt: Repair attempt number (max 3)
        
        Returns: True if repair succeeded, False if max attempts exceeded
        
        Logic:
          1. Analyze failures
          2. Determine root cause
          3. Regenerate only what failed (asset? composition? render settings?)
          4. Re-render
          5. Re-validate
          6. Return True if now valid, False otherwise
        """
```

### 7. PipelineOrchestrator — ✅ Implemented (Aug 2026)

**Purpose**: Execute a Director task schedule, dispatching each task to
the right component and tracking per-shot failures without one bad shot
halting the whole run.

**Status**: Real implementation in `src/core/orchestrator/orchestrator.py`,
97% test coverage (`tests/test_orchestrator.py`), including a real
end-to-end test — prompt → plan → assets → composed scenes → actual
rendered frames via a real Godot binary — gated behind
`BLAZENG_GODOT_BINARY` (see the Godot rendering notes under
SceneComposer above).

**What it actually does differently from the original sketch below**:
- Tasks execute **sequentially in schedule order**, not with real
  parallelism yet — Director's task schedule already groups all
  `generate_asset` tasks before any `compose_scene` tasks, etc., so the
  *ordering* guarantee holds, but nothing runs concurrently. True
  parallel asset generation is still open (see ROADMAP.md).
- Shot metadata (scene_description, characters, camera_angle, lighting,
  duration) is read back from `WorldStateManager` via `get_shot()`
  rather than threaded through task payloads — Director already seeds
  this when it builds a plan, so the orchestrator doesn't need its own
  copy.
- **Validation and repair are soft dependencies, not hard ones.** Phase 3
  (ValidatorManager, RepairEngine) isn't implemented — calling into a
  stub would just raise `NotImplementedError` with no useful behavior.
  So by default (`skip_validation: true`, which is also the config
  default) the orchestrator marks a shot `"rendered_unvalidated"` and
  moves on instead of crashing. Setting `skip_validation: false` in
  config.yaml switches it to actually call `validator_mgr.validate_frame()`
  — correct, honest behavior once Phase 3 lands, since that's exactly
  when the stub gets replaced with real logic.
- FFmpeg export (final step in the original sketch) isn't part of
  `run_pipeline()` yet — that's Phase 4, a separate step to be added
  once rendering + validation are both solid.

**Interface** (as actually implemented):
```python
class PipelineOrchestrator:
    def __init__(self, config, world_state, asset_manager, scene_composer,
                 validator_mgr, repair_engine, godot_renderer=None):
        """godot_renderer is optional at construction time — render tasks
        raise a clear OrchestratorError if it's None when actually needed,
        rather than requiring it even for asset-generation-only test runs."""

    def load_task_schedule(self, tasks: List[Task]) -> None: ...

    def run_pipeline(self) -> bool:
        """Runs every loaded task via generate_asset/compose_scene/render/
        validate handlers. A failure in one shot's task is recorded in
        self.failures (a list of ShotFailure) and does NOT stop other
        shots — returns True iff self.failures is empty at the end."""

    def cancel(self) -> None:
        """Clears remaining tasks; a subsequent run_pipeline() is then a no-op."""

    # UI hooks (optional, set directly as attributes):
    #   orchestrator.on_progress = lambda i, total: ...
    #   orchestrator.on_shot_update = lambda shot_id, status, details: ...
```

---

## Data Flow: From Prompt to Video

```
1. USER PROMPT
   "A detective discovers a clue at a warehouse"
   
   ↓
   
2. DIRECTOR PLANNING
   • Parse prompt → 15 shots
   • Each shot: characters, camera angle, lighting, action
   • Create task schedule (generate assets → compose → render → validate → export)
   
   ↓ (Shot structure with asset requirements)
   
3. WORLD STATE INITIALIZATION
   • Register characters: "detective", "warehouse", "key"
   • Store initial state
   
   ↓
   
4. ASSET GENERATION (parallel)
   • AssetManager.get_asset("character", "detective", "1940s detective with fedora...")
   • AssetManager.get_asset("object", "key", "brass key, 1940s style...")
   • AssetManager.get_asset("environment", "warehouse", "abandoned, rain, dramatic shadows...")
   
   ↓ (Paths to generated images)
   
5. SCENE COMPOSITION (sequential)
   • SceneComposer.compose_shot(shot_1, assets)
   • Creates Godot scene with detective PNG, warehouse PNG, key PNG
   • Sets camera angle, lighting, positioning
   • Returns path to .tscn scene file
   
   ↓ (Godot scene files)
   
6. RENDERING (sequential)
   • Launch Godot headless: godot --headless --script render_scene.gd scene.tscn
   • Renders 24fps × 5 seconds = 120 frames per shot
   • Outputs frames to storage/projects/{id}/shots/{shot_id}/frames/
   
   ↓ (PNG frame sequences)
   
7. VALIDATION (parallel)
   • ValidatorManager.validate_frame(frame, shot, world_state)
   • ScriptValidator: "Does this look like a detective at a warehouse?"
   • VisualValidator: "Is quality acceptable?"
   • CharacterValidator: "Is detective wearing fedora as expected?"
   • TemporalValidator: "Does this connect to previous shot?"
   
   ↓ (Pass/Fail per frame)
   
8. REPAIR (if needed, sequential)
   • For failed frames: RepairEngine.repair_shot(shot_id, failures)
   • Re-generate asset if character wrong
   • Re-compose scene if layout wrong
   • Re-render and re-validate (max 3 attempts)
   
   ↓ (All frames now valid)
   
9. EXPORT (sequential)
   • FFmpeg assembles frame sequence into video
   • Converts to MP4/ProRes
   • Exports OpenTimelineIO (Premiere/DaVinci timeline)
   • Output: storage/projects/{id}/output.mp4
```

---

## Provider Architecture

Three independent slots: **Text**, **Vision**, **Image**.

Each can use a different provider and API key.

### Text Provider (Story Planning)
```
gemini_provider.py        → google-genai SDK → Gemini 2.0 Flash
huggingface_provider.py   → transformers → Local model (Qwen, Phi, etc.)
ollama_provider.py        → requests → Local Ollama server
```

### Vision Provider (Frame Validation)
```
gemini_provider.py        → google-genai SDK → Gemini 2.0 Flash Vision
huggingface_provider.py   → LLaVA model locally
ollama_provider.py        → Ollama with vision capability
```

### Image Provider (Asset Generation)
```
gemini_provider.py        → google-genai SDK → Gemini 2.0 Flash (can generate images)
diffusers_provider.py     → Stable Diffusion XL Turbo (local)
```

**ProviderFactory** (single point of configuration):
```python
def get_text_provider(config):
    if config['text_provider'] == 'gemini':
        return GeminiProvider(config['gemini_api_key'], task='text')
    elif config['text_provider'] == 'huggingface':
        return HuggingFaceProvider(config['hf_repo_id'], task='text')
    # ... etc

# No code changes needed to swap providers — config-driven
```

---

## UI Architecture

Two-panel Qt6 layout:

### Left Panel (35%)
- **Prompt Input**: Text area for story prompt
- **Asset Browser**: Hierarchical view of generated assets (character/object/environment)
- **World State Viewer**: Current status of characters (appearance, injuries, props)
- **Settings**: Model configuration, output paths, etc.

### Right Panel (65%)
- **Timeline**: Horizontal shot strip
  - Each shot is a rectangle: grey (pending), green (done), red (failed)
  - Click to expand and see details
- **Shot Viewer**: When shot selected
  - Frame preview
  - Validation results (4 validators)
  - Asset versions used
  - Render settings
- **Preview Player**: Watch assembled video
- **Export Button**: Generate final video + timeline export

---

## Testing Strategy

**Unit Tests** (pytest):
```python
# tests/test_director.py
def test_director_parses_prompt():
    director = Director(mock_text_provider, mock_world_state)
    plan = director.generate_production_plan("A detective discovers a clue")
    assert len(plan.shots) > 0
    assert all(s.shot_id for s in plan.shots)

# tests/test_world_state.py
def test_world_state_tracks_character():
    ws = WorldStateManager(config)
    ws.add_character("john", {"appearance": "tall, brown hair"})
    assert ws.get_character("john")["appearance"] == "tall, brown hair"

# tests/test_asset_manager.py
def test_asset_manager_caches():
    am = AssetManager(config, mock_image_provider)
    path1 = am.get_asset("character", "john", "tall detective")
    path2 = am.get_asset("character", "john", "tall detective")  # same prompt
    assert path1 == path2  # same file, cache hit
```

**Integration Tests**:
```python
# tests/test_end_to_end.py
def test_full_pipeline():
    # 1. Create director
    # 2. Parse prompt
    # 3. Generate assets
    # 4. Compose scenes
    # 5. Validate frames
    # 6. Export
    # Assert final output exists and is valid
```

**Manual Testing**:
- Launch UI with mock providers
- Test shot timeline interaction
- Test model configuration dialog
- Test asset browser navigation

---

## Error Handling & Logging

**Logging Hierarchy**:
```
storage/logs/
├── studio.log          Main application log
├── director.log        Story planning
├── asset_manager.log   Asset generation
├── renderer.log        Godot output
├── validation.log      Validator results
└── repair.log          Repair engine attempts
```

**Error Levels**:
- `DEBUG`: Detailed execution flow (asset cache hit, LLM token count)
- `INFO`: Major milestones (shot generated, validation passed)
- `WARNING`: Recoverable issues (asset generation slow, retry #2)
- `ERROR`: Serious issues (LLM call failed, repair exhausted)
- `CRITICAL`: Fatal (no valid GPU, no disk space, pipeline aborted)

---

## Performance Considerations

**Bottlenecks**:
1. **LLM calls** (Director): 5-10 sec per shot plan
2. **Asset generation** (AssetManager): 30-60 sec per unique character/object
3. **Rendering** (Godot): 10-30 sec per shot (depends on resolution, effects)
4. **Validation** (Vision LLM): 2-5 sec per frame

**Parallelization**:
- ✅ Asset generation: All characters can generate simultaneously
- ✅ Validation: All rendered frames can validate simultaneously
- ❌ Rendering: Sequential (must wait for composed scenes)
- ❌ Composition: Sequential (must wait for assets)

**Caching**:
- ✅ Asset deduplication (same character = reuse image)
- ✅ Model weights (download once, reuse)
- 🔧 Scene composition caching (same location = cache scene?)
- 🔧 Validated frame caching (don't re-validate successful frames)

---

## Extension Points

1. **New Validators**: Inherit from `BaseValidator`, implement `validate(frame, shot, world_state)`
2. **New Providers**: Implement `TextProvider`, `VisionProvider`, `ImageProvider` interfaces
3. **New Integrations**: Add new tool bridges (Unreal, Blender native, etc.)
4. **Custom Repair Strategies**: Override `RepairEngine.repair_shot()` for domain-specific fixes

---

## Known Limitations & Future Work

**Current**:
- 2D rendering only (sprite-based)
- Limited character interactions
- No sound/music
- No lip-syncing

**Future**:
- 3D asset rigging with Blender
- Character animation cycles
- Multi-character interactions
- Audio narration + synchronization
- LLM-driven editing (user feedback loop)
- Distributed rendering (multiple GPUs)

---

**Last updated**: August 23, 2026
