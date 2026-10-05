# Changelog

## 0.11.0 — 2026-10-04

### Added
- **Rigged 3D characters in Blender** (`blender_characters: rigged` / `--characters rigged`).
  Each character is an armature-driven figure (hips, spine, neck, head, jaw, arms, legs) wearing the
  colours read from its generated art, with real animation: idle (breathing, sway), talk (nods and
  gestures that follow the speech) and walk (stride, arm swing, faces its direction of travel and
  moves toward the middle when the shot's action says walk/approach/enter). The **jaw follows the
  loudness of the dialogue audio**, frame by frame, for the speaker only.
- **Bring your own model**: `character_models: {Name: file.glb}` / `--model Name=file.glb`. The model
  is scaled to the standard height; a `jawOpen` / `mouthOpen` / `viseme_aa` shape key is driven by the
  speech and its own idle/walk/talk animation clips play. (On distro Blender builds the glTF
  importer needs numpy for Blender's Python: `apt install python3-numpy`.)
- **Voices**: `voices: {Name: voice}` / `--voice Name=voice` per character; `speech_provider: piper`
  (local neural voices from `.onnx` models in a `voices/` folder, multi-speaker `name:3`),
  `speech_provider: command` (any TTS program, e.g. a voice-cloning tool given a recording you have the
  right to use); `blazeng voices` lists what is available.
- Scene JSON gains optional rigged-character fields (palette, model, motion, per-frame mouth curves,
  dialogue timing); version stays 1 and old files render as before.

### Changed (desktop app)
- Settings dialog now exposes speech provider, per-character voices and Blender characters (planes/rigged).

### Fixed
- Walking characters faced away from their direction of travel (they walked backwards); the turn was
  the wrong way round. Tests now measure which way each figure faces, for the procedural rig and glTF models.

### Changed
- Speech is now produced *before* scenes are composed (scene composition needs its timing).
- Mouth envelope is normalised after smoothing so the loudest syllable opens the mouth fully.

### Known limits
- The procedural figure is a stylised mannequin, not a sculpted character; faces have eyes and a
  mouth only. Image planes remain the default.
- No viseme (mouth-shape) lip sync: the jaw opens with loudness. Piper voice models could not be
  downloaded in the development sandbox, so Piper is tested against its real CLI interface with a stand-in model.

## 0.10.0 — 2026-10-04

### Added
- **Dialogue and speech.** Shots can carry spoken lines (`dialogue` in the plan; the Director asks
  for it). A new `speak` stage synthesises each line, joins them per shot, lengthens the shot to fit,
  and the MP4 and the `.otio` timeline (new Dialogue audio track) include the audio.
  Providers: `espeak` (real offline speech via espeak-ng), `openai`, `dummy` (tone bursts, for tests),
  `none`; default `auto` = espeak-ng if installed, otherwise silent. CLI: `--speech`.
  One stable voice per character name.
- **Character consistency.** A new version of an existing character (changed description, repair,
  forced regeneration) is generated *from* its first image via `reference_image` (Gemini and OpenAI use
  it; other providers ignore it), measured against that reference, retried when it drifts, and flagged
  in the asset metadata. A deterministic `ConsistencyValidator` checks that each character's
  colours are visible in the rendered frame (hue-based, robust to lighting).
- `blazeng doctor` reports speech status.

### Changed
- `ImageProvider.generate_image` takes an optional `reference_image` (backwards compatible).
- Timeline durations now come from the frames actually rendered.

### Known limits
- Characters are still flat image cut-outs: no 3D models, rigs, body animation or mouth movement.
  Speech is a voice track over the picture, not lip sync.

## 0.9.1 — 2026-10-04

### Added
- Windows/macOS render path (xvfb-run only on Linux, no console flash).
- Installed-app support: per-user config/storage/logs, bundled ffmpeg/Godot defaults.
- PyInstaller bundle ships the GUI plus `blazeng-cli`; `scripts/smoke_frozen.py` tests the frozen app.
- CI: ruff lint, coverage gate, Windows/macOS test legs (experimental), frozen-build smoke test,
  installer build + silent install/uninstall check.

### Changed
- Installer rebranded to BlazEng, upgrade-safe, asks before deleting user data on uninstall.
- Removed unused imports; added ruff config.

## 0.9.0 — 2026-10-02

### Added
- **Blender render backend** (`renderer: godot | blender | auto`). Headless 3D
  rendering under Xvfb: character image planes on a floor and back wall,
  perspective camera chosen from the shot's camera angle with a slow push-in,
  sun + fill lighting from the shot's lighting. Engines: EEVEE (default),
  Cycles, Workbench. Verified against Blender 4.0.2.
- **Headless CLI** — `blazeng run | doctor | version` (also `python -m src.cli`),
  scriptable exit codes and `--json` output.
- **OpenTimelineIO export** — every run writes an `.otio` next to the MP4.
- **Desktop UI** — shot timeline, frame-scrubbing shot viewer, asset browser,
  world-state viewer, settings dialog, cancellable background worker.
- `pyproject.toml` (Hatch backend) and a `blazeng` console script; PyQt6 is an
  optional `ui` extra, so the CLI installs and runs without Qt.
- Renderer-neutral `<shot>.scene.json` written alongside each Godot `.tscn`.
- CI job running real Blender and a full CLI run on `main`.

### Changed
- Pipeline wiring moved to Qt-free `src/pipeline.py`; `main.py` delegates to it.
- `PipelineOrchestrator` accepts a backend-neutral `renderer=` (`godot_renderer=`
  still works) and exposes `rendered_frames`.
- Offline `dummy` providers now run the whole pipeline: the text provider
  returns a valid shot plan, the image provider draws coloured silhouettes.
- README, ROADMAP, `config.yaml` and the installer now describe the actual state.

### Fixed
- Test suite failed on a clean install (11 tests) because it depended on
  optional `transformers` / `diffusers` being installed.
- `PipelineOrchestrator.cancel()` did not stop a run already in progress.
- `WorldStateManager.list_shots()` sorted `shot_id` as a string (`shot_10` before `shot_2`); now natural order.
- Provider validation repeated the same missing-key message once per slot.
- VideoAssembler: wrong start frame for 1-based sequences (Blender) and failure
  on odd render dimensions.
- The original GUI run path could not work (nonexistent `window.timeline`,
  pipeline touched from the wrong thread, SQLite connection shared across threads).

### Known issues
- Package is still named `src` (installed as top-level `src`).
- Live cloud-provider calls, real local-model downloads, and importing the
  `.otio` into Premiere/Resolve remain unverified.
