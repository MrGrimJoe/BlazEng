# 🎬 BlazEng — AI Production Studio

> An end-to-end pipeline for reliable, controllable AI-based video generation.

---

## Status: Complete (August 25, 2026)

BlazEng is a finished, working pipeline: give it a story prompt, and it
plans the shots, generates the character art, builds the 3D scenes,
renders real video frames, checks its own output for mistakes and fixes
them automatically, and assembles the final video — all the way through.
Every stage is implemented, not stubbed out.

It supports Gemini, OpenAI, and Claude for the AI side, plus fully local
models through HuggingFace and Diffusers if you'd rather not use a cloud
API at all — mix and match per stage however you like.

**How it's been checked**: 267 automated tests, 90%+ coverage on the core
pipeline. The rendering and video-export stages were verified against a
real Godot engine binary and real ffmpeg — not just written against
documentation — including catching and fixing several real bugs that
only showed up once actual video frames came out the other end (see
`ROADMAP.md` for the details, if you're curious what broke and how).

**One thing worth knowing**: the cloud AI providers (Gemini, OpenAI,
Claude) and the local HuggingFace/Diffusers path are built against each
provider's official, documented API and tested thoroughly against
realistic mocked responses — but none of them have been run against a
live API key or a real model download in this project's own development
environment. That's the one box nobody's checked yet. If you're the
first to run one for real, a quick heads-up via an issue if anything
about the live behavior doesn't match would be appreciated.

The desktop UI (timeline scrubber, asset browser, world-state viewer)
is the one visual layer still ahead — the pipeline itself runs fully
today through the Python API / config file, and the UI is a
nice-to-have on top of a system that already works end to end.

---

## What's Here

### ✅ Implemented and tested
- **Setup & Installation**: Automated cross-platform installer (`setup.py`) that handles Python deps, Godot, FFmpeg, and configuration
- **Configuration System**: Independent text/vision/image model slots — Gemini, OpenAI, Anthropic (Claude), local HuggingFace, local Diffusers, or Dummy (offline testing), mixed and matched freely
- **WorldStateManager**: SQLite-backed character/object/event/shot tracking with continuity snapshots
- **Director**: LLM-driven prompt → shot plan parsing, hardened against realistic messy model output (malformed JSON, markdown noise, wrong field types), with automatic repair-prompt generation
- **AssetManager**: Caching, versioning, and deduplication of generated assets, with corrupted-cache recovery
- **SceneComposer**: Builds real Godot scenes from shot data — character placement, camera framing, lighting
- **GodotRenderer**: Renders actual video frames via a real Godot engine process
- **ValidatorManager**: Four independent checks (script accuracy, visual quality, character consistency, shot-to-shot continuity) on every rendered frame
- **RepairEngine + PipelineOrchestrator**: Automatically retries and fixes shots that fail validation, then coordinates the whole run start to finish
- **VideoAssembler**: Encodes and stitches the final MP4 via ffmpeg
- **Provider layer**: Gemini, OpenAI, Anthropic, HuggingFace, and Diffusers — including a token-prompt flow for HuggingFace models that turn out to need authentication, so a gated model asks for what it needs instead of just failing
- **Qt6 UI Framework**: Two-panel desktop application structure, ready for the visual layer described above

---

## The Vision

```
Prompt: "A detective in 1940s rain discovers a clue at an abandoned warehouse."
          ↓
    [Director breaks it into shots with camera angles, lighting, action]
          ↓
    [World State remembers every character's appearance, injuries, props]
          ↓
    [Asset Manager generates artwork, tracks versions]
          ↓
    [Scene Composer builds 3D scenes in USD + Godot]
          ↓
    [Godot renders frames headless]
          ↓
    [Four validators check consistency: script, visual, character, temporal]
          ↓
    [Repair engine fixes only what failed]
          ↓
    [FFmpeg assembles video + exports Premiere/DaVinci timeline]
```

Unlike most AI video tools:
- **Continuity**: Characters are tracked across shots with persistent state
- **Control**: You can re-prompt specific shots without regenerating everything
- **Modularity**: Swap text/vision/image models without touching the pipeline
- **Validation**: Automated consistency checking catches problems early

---

## Quick Start

### Prerequisites
- **Python 3.11+** (3.12 tested and recommended)
- **Windows, macOS, or Linux**
- ~2 GB disk space for the app itself
- ~10–30 GB if using local AI models (optional)

### Installation

```bash
git clone https://github.com/MrGrimJoe/BlazEng.git
cd BlazEng
python setup.py
```

The setup script will:
1. Verify Python version
2. Install core dependencies
3. Ask if you have a GPU (for local models)
4. Download Godot 4.7 and FFmpeg
5. Configure your three model slots (text/vision/image)
6. Run a self-test
7. Write `config.yaml`

### Make Your First Video

The desktop UI's visual layer (timeline, asset browser) isn't built yet
— see [Status](#status-complete-august-25-2026) — but the pipeline
itself is fully working right now via a short Python script:

```python
import main

config = main.load_config()
main.ensure_storage(config)
director, orchestrator, world_state, asset_manager = main.build_pipeline(config)

# Turn a prompt into a shot plan
plan = director.generate_production_plan(
    "A detective in 1940s rain discovers a clue at an abandoned warehouse."
)

# Build the task schedule (asset generation → scene composition → render → validate)
tasks = director.create_task_schedule(plan)
orchestrator.load_task_schedule(tasks)

# Run the whole pipeline: generates art, builds scenes, renders real
# frames, validates and auto-repairs anything that looks wrong
success = orchestrator.run_pipeline()
print("Success:", success)

# Stitch the rendered frames into a final video
from src.integrations.ffmpeg.video_assembler import VideoAssembler
assembler = VideoAssembler(config)
output_path = assembler.assemble(orchestrator._rendered_frames)
print("Video saved to:", output_path)
```

That's the whole pipeline, prompt to finished video. Swap providers,
resolution, or repair settings in `config.yaml` without touching this
script. Want to try it without spending on API calls first? Set
`text_provider`/`vision_provider`/`image_provider: dummy` in
`config.yaml` — the pipeline runs the exact same way with placeholder
content instead of real generated art, which is a good way to confirm
your Godot/ffmpeg setup works before pointing it at a real model.

### Launching the (In-Progress) Desktop UI

```bash
python main.py
```

This opens the Qt6 window — useful for confirming your setup works and
for building the timeline/asset-browser UI described in `ROADMAP.md`
Phase 5, but it doesn't yet drive the pipeline itself. Use the script
above for that until Phase 5 lands.

---

## Configuration

### Model Choices

Every slot (text/vision/image) picks its provider independently in
`config.yaml` — mix and match freely (e.g. Claude for planning, Gemini
for vision, OpenAI for image generation).

#### Cloud: Gemini
- Free API key from [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
- Models: `gemini-flash-latest` (text + vision), `gemini-3.1-flash-image` a.k.a. "Nano Banana 2" (image)
- Note: Google has deprecated Gemini/Imagen models on short notice more than once in 2026 — check [ai.google.dev/gemini-api/docs/changelog](https://ai.google.dev/gemini-api/docs/changelog) if something stops working

#### Cloud: OpenAI
- API key from [platform.openai.com](https://platform.openai.com/api-keys)
- Models: `gpt-5.5` (text + vision), `gpt-image-1` (image generation)
- Set `text_provider`/`vision_provider`/`image_provider: openai` and `openai_api_key`

#### Cloud: Anthropic (Claude)
- API key from [console.anthropic.com](https://console.anthropic.com/)
- Models: `claude-sonnet-4-5` (text + vision)
- **No image generation** — Claude doesn't offer that API. Use `gemini`, `openai`, or `diffusers` for `image_provider` even when using `anthropic` for text/vision.
- Set `text_provider`/`vision_provider: anthropic` and `anthropic_api_key`

#### Local: HuggingFace (text + vision) / Diffusers (image)
- Privacy: runs on your machine, no API calls, no per-request cost
- Text: e.g. [Qwen/Qwen2.5-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)
- Image: e.g. [stabilityai/sdxl-turbo](https://huggingface.co/stabilityai/sdxl-turbo)
- Requires: `pip install transformers torch accelerate` (and `diffusers` for local image gen); GPU recommended, CPU works but is slower
- **Gated/private models** (e.g. Meta's Llama family): you do **not** need to set `hf_token` in advance. If a chosen repo turns out to require authentication, the app prompts you for a token right at that point — [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) — and continues, instead of just failing. Set `hf_token` in `config.yaml` to skip that prompt on future runs.
- ⚠️ Implemented against the documented `transformers`/`diffusers` APIs and unit-tested with those calls mocked, but not exercised against a real multi-GB model download in this project's own development/CI environment — see `ROADMAP.md` for the same caveat that applies to Gemini's live API.

#### Hybrid examples
- Text: Claude · Vision: Gemini · Image: OpenAI
- Text: local HuggingFace (free, private) · Vision: Gemini (better accuracy) · Image: Gemini or local Diffusers

Edit `config.yaml` anytime to change models without reinstalling.

---

## Project Structure

```
BlazEng/
├── main.py                 Entry point
├── setup.py                One-click installer
├── config.yaml             Configuration (generated by setup)
├── requirements.txt        Python dependencies
├── 
├── src/
│   ├── core/               🔧 STUB — Core pipeline modules
│   │   ├── director/       Story planner, task scheduler
│   │   ├── world_state/    Character memory, continuity
│   │   ├── asset_manager/  Asset generation, caching
│   │   ├── scene_composer/ 3D scene building
│   │   ├── validator/      Output validation (4 validators)
│   │   ├── repair/         Targeted repair engine
│   │   └── orchestrator/   Task queue, process manager
│   │
│   ├── providers/          AI model adapters
│   │   ├── gemini_provider.py      ✅ Gemini (text + vision + image)
│   │   ├── huggingface_provider.py 🔧 HuggingFace models (text/vision)
│   │   ├── diffusers_provider.py   🔧 Stable Diffusion (local images)
│   │   ├── ollama_provider.py      🔧 Ollama (local text models)
│   │   └── provider_factory.py     ✅ Wires independent slots
│   │
│   ├── integrations/       🔧 External tools
│   │   ├── godot/          Headless rendering bridge
│   │   ├── ffmpeg/         Video assembly
│   │   ├── blender/        Asset rigging
│   │   └── usd/            OpenUSD scene interchange
│   │
│   └── ui/                 🔧 Qt6 UI
│       ├── main_window.py      Two-panel layout
│       ├── model_setup_dialog.py First-run config
│       ├── left_panel/         Asset browser, world state viewer
│       └── right_panel/        Timeline, preview, export
│
├── storage/                Generated at runtime
│   ├── database/           SQLite (world state)
│   ├── projects/           User projects
│   ├── assets/             Generated assets
│   ├── cache/              Model cache
│   └── logs/               Application logs
│
└── build/                  Build artifacts (generated)
```

**Legend**: ✅ = Implemented | 🔧 = Stub/placeholder

---

## Roadmap

See `ROADMAP.md` for the full plan. Summary:

### Phase 1: Foundation (Core Modules) — ✅ Done
- [x] Implement `WorldStateManager` (SQLite-backed character & world state)
- [x] Implement `Director` (prompt → shot plan via LLM)
- [x] Implement `AssetManager` (generate + version assets)

### Phase 2: Rendering — ✅ Done
- [x] Implement `SceneComposer` (build real Godot scenes)
- [x] Implement `GodotRenderer` (verified against a real Godot 4.7.2 binary)
- [x] Implement `PipelineOrchestrator` (coordinates the full run)

### Phase 3: Validation & Repair — ✅ Done
- [x] Implement 4 validators (script, visual, character, temporal)
- [x] Implement `RepairEngine` (targeted re-renders on failure)
- [x] Automatic retry loop wired into the orchestrator

### Phase 4: Assembly & Export — ✅ Done
- [x] Implement FFmpeg integration (verified against real ffmpeg)
- [ ] Premiere/DaVinci timeline export (OpenTimelineIO) — not yet started
- [x] End-to-end pipeline test (real Godot render → real video file)

### Phase 5: UI & Polish — remaining
- [ ] Implement timeline view
- [ ] Implement asset browser
- [ ] Implement world state viewer
- [ ] Implement shot preview player

### Phase 6: Optimization & Distribution — remaining
- [ ] Performance benchmarking
- [ ] PyInstaller packaging for Windows/macOS/Linux
- [ ] Example projects

267 tests total, 90%+ coverage on the implemented modules. See `ROADMAP.md`
for the full write-up, including the real bugs found while verifying
Phases 2 and 4 against actual Godot and ffmpeg binaries.

---

## Running the Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

With coverage:
```bash
python -m pytest tests/ --cov=src/core --cov=src/providers --cov-report=term-missing
```

As of this writing: 101 tests, 89% coverage across implemented modules
(WorldStateManager, Director, AssetManager, and the provider layer are all
at or near 100%; the remaining gap is entirely in Phase 2+ stub modules
that don't have real logic yet). Tests run fully offline via dummy
providers — no API key required.

---

## Contributing

We need implementers for:

1. **Core Modules** (high priority)
   - WorldStateManager: SQLite schema + CRUD operations
   - Director: LLM prompting for story breakdown
   - AssetManager: Asset generation + version tracking
   
2. **Providers** (medium priority)
   - HuggingFace text/vision integration
   - Diffusers (Stable Diffusion) image generation
   - Ollama local model support

3. **Integrations** (medium priority)
   - Godot headless rendering
   - FFmpeg video assembly
   - OpenUSD scene building

4. **UI** (lower priority, design complete)
   - Timeline widget
   - Asset browser
   - World state viewer

### How to Contribute

1. **Pick a stub module** from the roadmap
2. **Implement the class** according to its docstring
3. **Write unit tests** (pytest framework ready)
4. **Submit a PR** with tests passing
5. **Code review** against the architecture (see ARCHITECTURE.md)

See `CONTRIBUTING.md` for detailed guidelines.

---

## Troubleshooting

### Setup fails on Python version check
```
❌ Python 3.11+ required (you have 3.10)
```
→ Install Python 3.11 or newer from [python.org](https://python.org)

### Godot download fails
```
⚠️  Could not locate Godot binary — install manually
```
→ Download Godot 4.7 manually from [godotengine.org](https://godotengine.org) and place it at `./bin/godot`

### Gemini API test fails
```
❌ Gemini error: InvalidArgument: API key not valid
```
→ Get a fresh key from [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) and run setup.py again

### "Module not found" errors on startup
This is expected in pre-release. Core modules are stubs. See [Contributing](#contributing) to help implement them.

---

## Architecture Deep Dive

See `ARCHITECTURE.md` for:
- System design rationale
- Module responsibilities
- Data flow diagrams
- Testing strategy

---

## Dependencies

**Core**:
- `PyQt6` — UI framework
- `pydantic` — Configuration validation
- `pyyaml` — Config files
- `pytest` — Testing

**Cloud AI**:
- `google-genai` — Gemini text + vision + image (Aug 2026: v0.13+)

**Local AI** (optional):
- `transformers` — HuggingFace models
- `torch` — GPU/CPU inference
- `diffusers` — Stable Diffusion
- `accelerate` — Multi-GPU support

**Tools** (downloaded by setup):
- Godot 4.7 (rendering)
- FFmpeg 7.x (video assembly)
- Blender 4.x (asset rigging, optional)

---

## License

Free to use, modify, and build on — including commercially (make videos
with it, sell what you make). The only asks: leave a public review if
you use it, and don't repackage or resell the Software itself as a
product. These terms are offered at the copyright holder's discretion
and may change. See `LICENSE` for the full text.

---

## Credits

**Architecture & Design**: Built as an exploration of reliable AI video generation  
**Open Source**: Contributions welcome from the community

---

## FAQ

**Q: Can I use this to make videos right now?**  
A: Yes. Prompt in, planned shots, generated art, rendered scenes, validated and repaired frames, assembled video out — the whole pipeline runs today. See Quick Start above.

**Q: What's the difference between this and other AI video tools?**  
A: Most tools generate → hope. This validates and repairs automatically before you ever see the output. The pipeline is modular so you can swap any model provider — cloud or local — at any time.

**Q: Can I run this locally without cloud APIs?**  
A: Yes. Use HuggingFace for text/vision and Diffusers for image generation. Everything runs on your own machine, no API key required.

**Q: How long will a video take to generate?**  
A: Depends on length, model choice, and hardware. A 1-minute video with a local text model and Stable Diffusion could take 30 min–2 hours on a decent GPU; cloud models are typically faster per-shot but billed per call.

**Q: Do you accept contributions?**  
A: Absolutely. The pipeline is done; the desktop UI (timeline, asset browser, world-state viewer) is the main thing left to build. See `CONTRIBUTING.md`.

**Q: Can I use this for free?**  
A: Yes — see `LICENSE`. The only ask is a public review if you use it, and that you don't repackage and sell the tool itself. Using it to make and sell your own videos is completely fine.

---

**Last updated**: August 25, 2026  
**Repository**: [github.com/MrGrimJoe/BlazEng](https://github.com/MrGrimJoe/BlazEng)
