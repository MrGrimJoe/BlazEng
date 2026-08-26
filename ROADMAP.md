# Roadmap — BlazEng Implementation Plan

> Phase-by-phase plan to move from architecture to working system.

---

## Status Overview

| Phase | Focus | Status | Timeline |
|-------|-------|--------|----------|
| **1** | Core modules | ✅ **Done** (Aug 23, 2026) — WorldStateManager, Director, AssetManager, provider layer all implemented and tested (101 tests, 89% coverage at the time) | — |
| **2** | Rendering integration | ✅ **Done** (Aug 24, 2026) — SceneComposer, GodotRenderer, PipelineOrchestrator implemented and verified against a real, actually-downloaded Godot 4.7.2 binary (not just mocked) | — |
| **3** | Validation & repair | 🔧 Stub — next up | — |
| **4** | Assembly & export | 🔧 Stub | — |
| **5** | UI implementation | 🔧 Stub | — |
| **6** | Testing & polish | 📋 Planned | — |

**As of August 24, 2026**: The full pipeline runs end-to-end for real — prompt
→ shot plan → asset generation → scene composition → **actual Godot-rendered
video frames** — with dummy providers standing in for the LLM/image-gen
calls (so it's testable offline) but genuine Godot rendering underneath.
159 tests pass, 95% coverage, including two tests that shell out to a real
Godot binary rather than mocking the subprocess call. Phases 3–6 remain stubs.

---

## Phase 1: Core Modules (Foundation) — ✅ DONE

**Goal**: Get the pipeline's "brain" working.

### 1.1 WorldStateManager — ✅ Done
- [x] Create SQLite schema (characters, states, events, shots)
- [x] Implement CRUD operations
- [x] Add continuity validation logic (latest-state-snapshot merging)
- [x] Write unit tests (16 tests, 98% coverage, including a transactional-
      rollback test proving failed writes don't partially commit)
- **Effort spent**: implemented and tested in one session
- **Definition of Done**:
  - [x] Character state is persistent (verified via reopen-across-instances test)
  - [x] Can query state at any shot
  - [x] Tests pass

### 1.2 Director — ✅ Done
- [x] Implement LLM-based prompt parsing (JSON-structured, with markdown-fence
      and prose-wrapper extraction, plus one retry on unparseable output)
- [x] Break narrative into shots with metadata
- [x] Generate task schedule with proper dependencies (assets deduplicated
      across shots sharing a character)
- [x] Handle repair prompts (re-prompt specific shots)
- [x] Write unit tests (23 tests, 100% coverage)
- **Definition of Done**:
  - [x] Parse simple prompts → valid shot plans (verified against dummy
        provider; real Gemini JSON-mode behavior unverified — see note below)
  - [x] Task schedule is topologically correct
  - [x] Repair prompts are sensible

### 1.3 AssetManager — ✅ Done
- [x] Implement asset generation with image provider
- [x] Add versioning (asset_name/v1, v2, etc.)
- [x] Implement caching and deduplication (keyed on description match)
- [x] Track asset usage in world state
- [x] Write unit tests (17 tests, 100% coverage, including corrupted-cache
      and missing-file recovery paths)
- **Definition of Done**:
  - [x] Assets are cached correctly
  - [x] Versions are tracked
  - [x] Duplicates are detected and reused

### Phase 1 Success Criteria — ✅ Met
- [x] All three modules implemented and tested
- [x] Can parse prompt → world state → task schedule → assets (proven via
      `tests/test_integration_pipeline.py` end-to-end test)
- [x] Zero import errors (stubs are now real)

### Known gap: live Gemini API unverified
The `GeminiTextProvider`/`GeminiVisionProvider`/`GeminiImageProvider` classes
are written against the google-genai SDK's documented interface (client
construction, `generate_content()`, `response_modalities`) and unit-tested
against a mocked client — but no live call to
`generativelanguage.googleapis.com` has been made, because this development
sandbox's network policy blocks that host. **Before relying on these in
production, run one real call against each of the three Gemini providers
with a valid API key** to confirm the SDK's actual live behavior matches
what the docs (and therefore this code) assume.

---

## Phase 2: Rendering Integration — ✅ DONE

**Goal**: Turn scenes into frames.

**Status**: Done and verified against a real Godot 4.7.2 binary — not
just written against documentation. Two design assumptions in the
original architecture turned out to be wrong once actually tested; see
below.

### 2.1 SceneComposer — ✅ Done
- [x] Build Godot scene (.tscn) files from shot metadata
- [x] Position characters left-to-right, scaled by camera angle
- [x] Lighting keyword → scene-wide color modulate
- [x] 2D sprite composition via runtime image loading (see finding #1 below)
- [x] Configurable render resolution (render_width/render_height), verified
      to actually change rendered pixel dimensions
- [x] 27 unit tests, 100% coverage
- **Definition of Done**: all met — generates valid, Godot-loadable scene
      files; characters positioned correctly; lighting/camera reflected;
      confirmed by actually rendering generated scenes, not just inspecting
      their text.

### 2.2 Godot Rendering Bridge — ✅ Done
- [x] Headless(-ish — see finding #2) rendering invocation via subprocess
- [x] Frame output via Godot's built-in `--write-movie` PNG sequence writer
- [x] Handles: missing binary, missing xvfb-run, timeout, non-zero exit,
      zero-frames-produced, stale-frame cleanup
- [x] 10 unit tests (8 mocked + 2 real-Godot integration tests, gated
      behind `BLAZENG_GODOT_BINARY` env var so CI doesn't need a ~150MB
      binary), 100% coverage
- **Definition of Done**: all met — verified with actual rendered pixel
      output at both default and custom resolutions.

### 2.3 PipelineOrchestrator — ✅ Done
- [x] Load and execute a Director task schedule in order
- [x] Dispatch generate_asset / compose_scene / render / validate to the
      right component
- [x] Per-shot failure isolation — one shot failing doesn't abort the rest
- [x] Progress/status hooks for future UI wiring (`on_progress`, `on_shot_update`)
- [x] Honest validation stub-skipping (`skip_validation: true` by default,
      since ValidatorManager isn't implemented yet — see Phase 3)
- [x] 21 unit tests including one real-Godot full-pipeline integration test,
      97% coverage
- **Definition of Done**: all met — confirmed with a genuine end-to-end run
      (2 shots, 7 tasks, 0 failures, real rendered frames on disk).

### Real findings from hands-on verification (not in the original architecture doc)

1. **`ExtResource` doesn't work for freshly-generated images.** Godot's
   resource loader expects an `.import` sidecar file, which only exists
   after an image has been opened once in the Godot editor. A raw PNG
   fresh off an image-gen API has no such file and fails to load as a
   static `ExtResource`. Fix: SceneComposer generates a GDScript that
   loads images at runtime via `Image.load()` + `ImageTexture.create_from_image()`
   instead — confirmed working against a real render.

2. **`--headless` crashes on `--write-movie` with real visual content.**
   `--headless` forces Godot's "dummy" rendering driver, which has no
   actual rasterization pipeline — asking it to write a movie frame with
   a textured sprite in it segfaults (`texture_2d_get`: Parameter "t" is
   null). Fix: render under a virtual X display (`xvfb-run`) with a real
   software renderer (`--rendering-driver opengl3`, backed by llvmpipe on
   a GPU-less machine) — this genuinely renders and writes correct frames.

3. **A relative `--write-movie` path resolves against the Godot project
   directory, not the calling process's working directory.** With the
   default `storage_path: ./storage` in config.yaml, a naively-relative
   frame output path silently wrote frames to
   `<project_dir>/storage/renders/...` instead of the real storage
   location — the renderer then correctly reported "no frames found"
   because it was looking in the right place for the wrong output. Fix:
   `GodotRenderer` now always resolves `--write-movie` and `--path` to
   absolute paths before building the command.

4. **`config.yaml` had a stale key name** (`godot_path`) that didn't match
   what `GodotRenderer` actually reads (`godot_binary_path`) — a
   configured custom binary path would have been silently ignored in
   favor of the `./bin/godot` default. Fixed to match.

5. **A scene file passed as a positional CLI argument overrides the
   project's `run/main_scene`** — confirmed by rendering two different
   scenes from one shared project and getting different pixel output.
   This means one Godot project serves the whole production; each render
   just points at a different generated `.tscn`, rather than needing a
   separate project per shot.

### Phase 2 Success Criteria — ✅ Met
- [x] Compose scene from assets
- [x] Render frames using Godot (via Xvfb, not true `--headless` — see finding #2)
- [x] Orchestrator coordinates everything
- [x] End-to-end: prompt → assets → scenes → real rendered frames — proven
      with an actual run producing 96 real PNG frames per shot

---

## Phase 3: Validation & Self-Healing

**Goal**: Catch errors and fix them automatically.

### 3.1 ValidatorManager
- [ ] Implement ScriptValidator (LLM: "Does frame match description?")
- [ ] Implement VisualValidator (LLM: "Is visual quality acceptable?")
- [ ] Implement CharacterValidator (Vision + world state: "Are characters correct?")
- [ ] Implement TemporalValidator (Vision: "Does it connect to previous shot?")
- [ ] Add fuzzy response matching (handle model variation)
- [ ] Write 12+ unit tests
- **Owner**: Needed!
- **Effort**: 2-3 weeks
- **Depends on**: Text/vision providers working
- **Blocking**: RepairEngine
- **Definition of Done**:
  - All 4 validators run and return sensible results
  - Fuzzy matching handles local model variation
  - Tests cover edge cases

### 3.2 RepairEngine
- [ ] Analyze validation failures
- [ ] Determine root cause (asset? composition? rendering?)
- [ ] Implement targeted regeneration (don't remake everything)
- [ ] Add retry logic (max 3 attempts per shot)
- [ ] Track repair attempts in world state
- [ ] Write 8+ unit tests
- **Owner**: Needed!
- **Effort**: 1-2 weeks
- **Depends on**: ValidatorManager
- **Blocking**: Full pipeline with error recovery
- **Definition of Done**:
  - Repair attempts are targeted
  - Retry logic respects max attempts
  - Failed frames are logged with reason

### Phase 3 Success Criteria
- [ ] Validate all rendered frames
- [ ] Identify specific failures (script vs. visual vs. character vs. temporal)
- [ ] Repair failures targeted (asset? composition? render?)
- [ ] Max 3 repair attempts enforced

---

## Phase 4: Assembly & Export

**Goal**: Turn frame sequences into videos.

### 4.1 FFmpeg Integration
- [ ] Frame sequence → MP4 (H.264)
- [ ] Support multiple codecs (ProRes optional)
- [ ] Handle frame rate, resolution, bitrate
- [ ] Add error handling (missing frames, encoding failures)
- [ ] Write 4+ unit tests
- **Owner**: Needed!
- **Effort**: 1 week
- **Depends on**: Rendered frames from Phase 2
- **Blocking**: Premiere/DaVinci timeline export
- **Definition of Done**:
  - Generates valid MP4 video
  - Correct frame rate and resolution
  - Errors handled gracefully

### 4.2 OpenTimelineIO Export
- [ ] Generate `.otio` timeline from shot metadata
- [ ] Support Premiere and DaVinci Resolve import
- [ ] Include transitions and timing
- [ ] Write 4+ unit tests
- **Owner**: Needed!
- **Effort**: 1-2 weeks
- **Depends on**: Shot metadata, otio library
- **Blocking**: Non-critical (nice-to-have for editing)
- **Definition of Done**:
  - `.otio` files open in Premiere
  - Clips and timing are correct
  - Ready for manual editing

### Phase 4 Success Criteria
- [ ] Final video exported as MP4
- [ ] OpenTimelineIO timeline for editing
- [ ] Full pipeline: prompt → video + editable timeline

---

## Phase 5: User Interface

**Goal**: Make it beautiful and usable.

### 5.1 Timeline Widget
- [ ] Shot visualization (rectangles with status: grey/green/red)
- [ ] Click-to-select functionality
- [ ] Expand shot details
- [ ] Show validation results
- [ ] Write 4+ widget tests
- **Owner**: Needed!
- **Effort**: 1-2 weeks
- **Depends on**: Qt6, orchestrator signals
- **Blocking**: None (UI enhancement)
- **Definition of Done**:
  - Timeline renders correctly
  - Clicks work
  - Status colors update in real-time

### 5.2 Asset Browser
- [ ] Hierarchical tree view (Type → Name → Versions)
- [ ] Show asset previews (thumbnails)
- [ ] Version comparison
- [ ] Right-click menu (delete version, regenerate, etc.)
- [ ] Write 4+ widget tests
- **Owner**: Needed!
- **Effort**: 1-2 weeks
- **Depends on**: Asset manager, file system
- **Blocking**: None (UI enhancement)
- **Definition of Done**:
  - Browse all generated assets
  - Compare versions
  - Delete/regenerate from UI

### 5.3 World State Viewer
- [ ] Character list with current state
- [ ] Appearance, clothing, injuries, props
- [ ] Update in real-time as pipeline runs
- [ ] Write 4+ widget tests
- **Owner**: Needed!
- **Effort**: 1 week
- **Depends on**: WorldStateManager signals
- **Blocking**: None (UI enhancement)
- **Definition of Done**:
  - Shows all characters and state
  - Updates in real-time
  - Easy to understand

### Phase 5 Success Criteria
- [ ] Timeline widget functional
- [ ] Asset browser working
- [ ] World state viewer showing data
- [ ] UI responds to pipeline events

---

## Phase 6: Testing & Polish

**Goal**: Stable, documented, ready for users.

### 6.1 Test Coverage
- [ ] Achieve >80% code coverage
- [ ] Write end-to-end tests (full prompt → video)
- [ ] Test error paths (missing files, API failures, timeouts)
- **Owner**: Needed!
- **Effort**: 2 weeks
- **Blocking**: Release

### 6.2 Documentation
- [ ] Example projects and walkthroughs
- [ ] Troubleshooting guide
- [ ] API documentation
- [ ] Video tutorials (optional but nice)
- **Owner**: Needed!
- **Effort**: 1-2 weeks

### 6.3 Performance Optimization
- [ ] Profile pipeline with real workloads
- [ ] Optimize slow paths (LLM caching, asset dedup)
- [ ] Benchmark on different hardware
- **Owner**: Needed!
- **Effort**: 1-2 weeks

### 6.4 Release Preparation
- [ ] Version bump to 1.0.0
- [ ] Changelog generation
- [ ] Build and test installers (Windows, macOS, Linux)
- [ ] GitHub release
- **Owner**: Maintainers
- **Effort**: 1 week

### Phase 6 Success Criteria
- [ ] >80% test coverage
- [ ] Documentation is complete
- [ ] Installers work on all platforms
- [ ] Ready for public use

---

## Timeline

**Ideal scenario** (with full team):
```
Aug 2026: Architecture, stubs, documentation (✅ done)
Sep-Oct:  Phase 1-2 (Core + Rendering, 6-8 weeks)
Nov:      Phase 3-4 (Validation + Export, 4 weeks)
Nov-Dec:  Phase 5-6 (UI + Polish, 4-6 weeks)
Jan 2027: Release 1.0.0 🎉
```

**Realistic scenario** (with volunteers):
```
Aug-Dec 2026: Phase 1-2 (Core modules + rendering)
Dec 2026-Jan 2027: Phase 3-4 (Validation + export)
Feb-Mar 2027: Phase 5-6 (UI + polish)
Apr 2027: Release 1.0.0 🎉
```

**Conservative scenario** (limited contributors):
```
Aug 2026-Mar 2027: Phases 1-3 (Core → Validation)
Apr-Jun 2027: Phase 4-5 (Export + UI)
Jul 2027: Phase 6 (Polish + Release)
```

---

## How to Contribute

1. **Pick a phase/task** from above
2. **Check issue tracker** to see if anyone's already working on it
3. **Claim it** with a comment
4. **Implement** (follow `CONTRIBUTING.md`)
5. **Submit PR** with tests
6. **Celebrate** when merged! 🎉

---

## Dependency Graph

```
Phase 1 (Core)
  ├─ WorldStateManager
  │   └─ needed by: Director, AssetManager
  ├─ Director
  │   └─ needed by: Orchestrator
  └─ AssetManager
      └─ needed by: SceneComposer

Phase 2 (Rendering)
  ├─ SceneComposer
  │   ├─ depends on: AssetManager, Director
  │   └─ needed by: Godot bridge
  ├─ Godot Rendering Bridge
  │   └─ needed by: Validators
  └─ PipelineOrchestrator
      ├─ depends on: WorldStateManager, Director, AssetManager
      └─ needed by: Validators, Repair engine

Phase 3 (Validation)
  ├─ ValidatorManager
  │   ├─ depends on: Rendered frames from Phase 2
  │   └─ needed by: RepairEngine
  └─ RepairEngine
      └─ depends on: ValidatorManager

Phase 4 (Export)
  ├─ FFmpeg integration
  │   └─ depends on: Rendered frames
  └─ OpenTimelineIO export
      └─ depends on: Shot metadata

Phase 5 (UI)
  ├─ Timeline widget
  │   └─ depends on: Orchestrator signals
  ├─ Asset browser
  │   └─ depends on: Asset manager
  └─ World state viewer
      └─ depends on: WorldStateManager
```

**Green path to working system**:
1. Implement Phase 1 (3-4 weeks) → Pipeline has a "brain"
2. Implement Phase 2 (3-4 weeks) → Can render frames
3. Implement Phase 3 (2-3 weeks) → Self-healing pipeline
4. Implement Phase 4 (2 weeks) → Generate final videos
5. UI (1-2 weeks) → Make it pretty
6. Polish (2 weeks) → Ready!

---

## Success Metrics

When each phase is done:

**Phase 1**: Can run `director.generate_production_plan()` and get a valid shot plan  
**Phase 2**: Can render a shot into frames  
**Phase 3**: Validators pass/fail correctly; repairs work  
**Phase 4**: Final MP4 video generated  
**Phase 5**: UI is responsive and usable  
**Phase 6**: 80%+ test coverage; release ready  

---

## Help Wanted

- 🐍 Python developers (core logic)
- 🎨 Qt/UI developers (interface)
- 📹 Godot developers (rendering)
- 🧪 Test engineers (coverage, edge cases)
- 📚 Technical writers (docs, examples)
- 🚀 DevOps (CI/CD, packaging)

---

## Questions?

- **Architecture**: Read `ARCHITECTURE.md`
- **Contributing**: Read `CONTRIBUTING.md`
- **Status**: Check GitHub issues
- **Ideas**: Open a discussion

---

**Let's build this together!**

---

*Last updated: August 23, 2026*
