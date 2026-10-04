# Changelog

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
