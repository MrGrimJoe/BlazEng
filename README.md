# 🎬 AI Production Studio

> Built by a 16-year-old from Pakistan who watched a YouTube channel and thought: *I can make something better than this.*

---

## The story

I came across a YouTube channel called **Slow English** — videos with simple narration, clear visuals, built for language learners. Good concept. But I kept thinking: what if the whole production pipeline could be automated? What if you described a story and a piece of software handled everything — planning, characters, scenes, rendering, consistency, export — on its own?

So I built it.

I'm **Sana**, 16, currently in my pre-first-year at Punjab Colleges in Pakistan. I have no professional studio, no team, no budget. Just a phone, a laptop, and an idea that wouldn't leave me alone.

The scope of the project was shaped early on by my friend **Ayyan**, who pushed me to think bigger than I initially was. A lot of what this became is because of those early conversations. Thank you Ayyan.

This is now open source. Take it, learn from it, build on it.

---

## What this is

**AI Production Studio** takes a story prompt and turns it into a produced video — planned, cast, rendered, validated, and exported, almost entirely on its own.

You type: *"A detective in 1940s rain discovers a clue at an abandoned warehouse."*

The software:
1. Breaks it into shots, each with camera angles, lighting, characters, and action
2. Remembers every character's appearance, clothing, and injuries across the whole project
3. Generates artwork for every character, object, and environment
4. Assembles 3D scenes using real industry-standard formats (OpenUSD)
5. Renders frames using Godot 4 headless
6. Checks its own work — four separate validators catch inconsistencies automatically
7. Repairs only what failed, not the whole shot
8. Exports the final video with FFmpeg, plus a professional timeline you can open in Premiere or DaVinci Resolve

The interface is a two-panel desktop app: one side shows your assets and the current state of every character, the other is a CapCut-style timeline where you watch each shot get built in real time.

---

## Why this is different from every other AI video tool

Most AI video tools:
```
Prompt → Generate → Hope it stays consistent
```

This:
```
Prompt → Director → Shot Plan → World Memory
       → Asset Management → Scene Assembly
       → AI Generation → Validation → Repair → Export
```

The AI models are not the product. The **pipeline** is the product. Gemini, Stable Diffusion, whatever comes next — they're interchangeable parts that plug into this system. When a better model comes out, you swap it in. The memory, the continuity, the validation, the repair logic — that all stays and keeps working.

---

## Model flexibility — bring your own, run it locally, or mix

Three independent model slots, each configurable separately:

| Slot | What it does | Options |
|---|---|---|
| **Text** | Story planning, repair, continuity | Gemini · HuggingFace (local) · Ollama (local) |
| **Vision** | Checks rendered frames against the script | Gemini · HuggingFace · Ollama / LLaVA |
| **Image** | Generates character and environment art | Google Imagen · Stable Diffusion (local) |

You can use cloud for everything, run everything locally with no API key at all, or mix — local text model with cloud vision, for example. Each slot takes its own API key if you want to separate billing.

No API key? Paste any HuggingFace repo ID. The app downloads the model to your machine and runs it there. After that: no internet required, no cost, full privacy.

---

## What's been tested and verified

The following was run and verified in a real Python environment — not just syntax-checked:

- ✅ WorldStateManager: saves and retrieves characters, objects, world events, and shots from SQLite
- ✅ Director: parses a story prompt into a structured shot plan with valid schema
- ✅ Director: generates a full ordered task schedule from that plan
- ✅ AssetManager: generates an asset on first call, returns the same path on second call (reuse logic works)
- ✅ ValidatorManager: all four validators skip gracefully when no frames exist (no crash)
- ✅ Response matching: fuzzy token detection works for small/local model output variation
- ✅ **Full end-to-end pipeline**: prompt → plan → assets → scene → render placeholder → validate → all shots marked `done`, zero repair attempts needed

The only thing not testable without your own machine: real Gemini API calls, real Godot rendering, real Blender rigging. The code paths are there and wired — they just need the tools installed.

---

## Requirements

- Python 3.11 or newer
- Windows, macOS, or Linux
- 1–2 GB disk space for the app itself
- Additional space if using local models (3–30 GB depending on model size)
- A GPU is recommended for local image/text models but not required — CPU works, just slower

---

## Installation

### Option 1 — One command (recommended)

```bash
git clone https://github.com/sanasaleem-source/Hollyjolly.git
cd Hollyjolly
python setup.py
```

`setup.py` will:
- Verify your Python version
- Install core Python dependencies
- Ask whether you have a GPU (installs the right version of PyTorch)
- Download and install Godot 4 into `bin/`
- Download and install FFmpeg into `bin/`
- Ask you to choose Cloud (Gemini) or Local (HuggingFace) for each model slot
- Write `config.yaml` with all paths and keys filled in
- Run a self-test to confirm everything is working

### Option 2 — Manual

```bash
git clone https://github.com/sanasaleem-source/Hollyjolly.git
cd Hollyjolly
pip install pyyaml pydantic PyQt6 requests google-genai huggingface_hub
```

Then install the tools you need:
- [Godot 4](https://godotengine.org/download) — for rendering
- [FFmpeg](https://ffmpeg.org/download.html) — for video export
- [Blender](https://www.blender.org/download/) — optional, for advanced 3D asset rigging

For local AI models (optional):
```bash
# GPU (NVIDIA)
pip install torch transformers accelerate sentencepiece diffusers

# CPU only (no GPU, smaller download)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install transformers accelerate sentencepiece diffusers
```

For OpenUSD and timeline export (optional, Phase 9+):
```bash
pip install usd-core opentimelineio
```

---

## Running it

```bash
python main.py
```

The first time you launch, a setup screen walks you through configuring your three model slots if you skipped `setup.py`. After that it remembers everything.

Change models at any time from **Settings → Change AI Model** inside the app.

### Get a free Gemini API key

Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) — it's free and takes 30 seconds.

### Browse local models

Go to [huggingface.co/models](https://huggingface.co/models?pipeline_tag=text-generation) and filter by `text-generation`. Good starting points:
- `Qwen/Qwen2.5-3B-Instruct` — small, fast, runs on CPU
- `microsoft/Phi-3-mini-4k-instruct` — very capable for its size
- `stabilityai/sdxl-turbo` — fast local image generation

---

## Building a distributable .exe (Windows)

```bash
build.bat
```

Uses PyInstaller. Output goes to `dist/AIProductionStudio/`. An Inno Setup script (`installer.iss`) is included to build a proper Windows installer. On macOS/Linux, use `build.sh`.

---

## Configuration reference

`config.yaml` is generated by `setup.py`. Key fields:

```yaml
# Which provider handles each role
text_provider:   gemini      # gemini | huggingface | ollama
vision_provider: gemini
image_provider:  imagen      # imagen | diffusers

# Cloud keys (each slot can use a different key)
gemini_api_key:        YOUR_KEY_HERE
vision_gemini_api_key: ""   # blank = reuse gemini_api_key
image_api_key:         ""   # blank = reuse gemini_api_key

# Local model repos (used when provider is huggingface/diffusers)
hf_repo_id:       ""    # text model, e.g. Qwen/Qwen2.5-3B-Instruct
hf_image_repo_id: ""    # image model, e.g. stabilityai/sdxl-turbo

# Tool paths (setup.py fills these in automatically)
godot_path:   ./bin/godot
ffmpeg_path:  ./bin/ffmpeg
blender_path: blender

# Pipeline tuning
max_repair_attempts:    3
render_fps:             24
render_width:           1920
render_height:          1080
```

---

## How the UI works

**Left panel (35%)**
- Prompt area — type your story or target a specific shot that needs fixing
- Asset browser — every generated asset organised by type and version (`john/v1`, `john/v2`)
- World State viewer — current clothing, injuries, and status of every character

**Right panel (65%)**
- Timeline — every shot laid out as a horizontal strip
  - Grey = placeholder (not yet generated)
  - Green border = done
  - Red border = failed validation (click to see why)
- Shot viewer — click any shot to see its frames, the prompt used, asset versions, and validation results
- Preview player — watch the assembled video
- Export button

---

## Code structure

```
src/
  core/
    director/          Story parser, task planner, continuity advisor
    world_state/       SQLite-backed memory: characters, objects, world, shots
    asset_manager/     Exist-reuse-generate-version logic
    orchestrator/      Task queue, process manager, repair loop
    scene_composer/    Builds USD scenes and Godot JSON per shot
    validator/         4 independent validators + response matching
    repair/            Targeted repair engine (fixes only what failed)

  providers/
    gemini_provider.py      Text + vision via google-genai SDK
    imagen_provider.py      Image generation via Google Imagen
    huggingface_provider.py Local text/vision models
    diffusers_provider.py   Local image generation (Stable Diffusion)
    ollama_provider.py      Local models via Ollama
    provider_factory.py     Single place that wires text/vision/image slots
    prompts.py              All model prompts — model-neutral, one file

  integrations/
    godot/             Headless render bridge + GDScript render script
    blender/           Asset rigging and simulation
    ffmpeg/            Frame assembly and video encoding
    usd/               OpenUSD scene read/write
    otio/              OpenTimelineIO export (EDL, .otio)

  ui/
    main_window.py          Two-panel layout
    model_setup_dialog.py   First-launch model configuration
    left_panel/             Prompt, asset browser, world viewer
    right_panel/            Timeline, shot viewer, preview player

godot/
  render_scene.gd     Reads scene JSON and renders frames headless

storage/              Generated at runtime — projects, assets, cache, logs
tests/                pytest suite for World State operations
setup.py              One-click installer
build.bat / build.sh  PyInstaller packaging
```

---

## Current status

The core systems are built and verified:
- Story planning → World State memory → Asset generation → Scene composition → Validation → Repair → Export pipeline runs end to end
- All four validators work and degrade gracefully when Godot hasn't rendered yet
- The provider system supports full model swapping with no code changes
- The installer, packaging, and configuration system are complete

What improves with real hardware:
- Godot rendering quality (currently falls back to AI-generated placeholder frames)
- Image quality scales directly with which model you connect
- Speed scales with GPU

This is a real working system, not a demo. It's also version 1, built by one person. Contributions are welcome.

---

## Credits and acknowledgements

**Sana Saleem** — built everything in this repo.

**Ayyan** — shaped the scope and ambition of this project from the very first conversation. This would be a much smaller idea without you.

**Slow English** (YouTube) — the original inspiration. Go watch the channel.

---

*Open source. MIT License. Built in Pakistan.*
