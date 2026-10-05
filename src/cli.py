"""
BlazEng command line interface — runs the whole pipeline with no GUI and no
Qt dependency, so it works on servers, in CI and over SSH.

    python -m src.cli run "A detective finds a clue in a rainy alley" --renderer blender
    python -m src.cli doctor
    python -m src.cli version

Exit codes for ``run``:
    0  every shot rendered and the video was assembled
    1  video assembled, but one or more shots failed (see the summary)
    2  nothing usable was produced (config/provider/render failure)
    3  bad usage or unreadable configuration
"""

import argparse
import json
import logging
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import __version__
from src.pipeline import build_pipeline, ensure_storage, load_config, timeline_shots

EXIT_OK, EXIT_PARTIAL, EXIT_FAILED, EXIT_USAGE = 0, 1, 2, 3


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blazeng", description="BlazEng — prompt to video, headless."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="generate a video from a story prompt")
    run.add_argument("prompt", nargs="?", help="story prompt (or use --prompt-file)")
    run.add_argument("--prompt-file", help="read the prompt from a text file")
    run.add_argument("-c", "--config", default="config.yaml", help="config file (default: config.yaml)")
    run.add_argument("--storage", help="override storage_path")
    run.add_argument("--renderer", choices=["auto", "godot", "blender"], help="render backend")
    run.add_argument("--engine", choices=["eevee", "cycles", "workbench"], help="Blender engine")
    run.add_argument("--resolution", metavar="WxH", help="render size, e.g. 1280x720")
    run.add_argument("--fps", type=int, help="frames per second")
    run.add_argument("--dummy", action="store_true",
                     help="use offline placeholder providers (no API keys, no cost)")
    run.add_argument("--characters", choices=["planes", "rigged"],
                     help="Blender characters: flat image planes (default) or rigged, animated 3D figures")
    run.add_argument("--model", action="append", default=[], metavar="NAME=FILE.glb",
                     help="use your own glTF/GLB model for a character (implies --characters rigged; repeatable)")
    run.add_argument("--speech", choices=["auto", "none", "dummy", "espeak", "openai", "piper", "command"],
                     help="text-to-speech for dialogue (default: espeak-ng if installed, else silent)")
    run.add_argument("--voice", action="append", default=[], metavar="NAME=VOICE",
                     help="assign a voice to a character, e.g. --voice Ann=en_US-amy-medium (repeatable)")
    run.add_argument("--validate", action="store_true",
                     help="enable vision validation + auto-repair (needs a vision-capable provider)")
    run.add_argument("-o", "--output", default="final.mp4", help="output video name (default: final.mp4)")
    run.add_argument("--no-timeline", action="store_true", help="skip the .otio timeline export")
    run.add_argument("--json", action="store_true", help="print a machine-readable summary")

    voices = sub.add_parser("voices", help="list the voices the speech provider can use")
    voices.add_argument("-c", "--config", default="config.yaml")
    voices.add_argument("--speech", choices=["auto", "none", "dummy", "espeak", "openai", "piper", "command"])

    sub.add_parser("doctor", help="check this machine can run BlazEng").add_argument(
        "-c", "--config", default="config.yaml")
    sub.add_parser("version", help="print the version")
    return parser


def _parse_resolution(text: str) -> Tuple[int, int]:
    try:
        w, h = text.lower().split("x")
        w, h = int(w), int(h)
        if w < 16 or h < 16:
            raise ValueError
        return w, h
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid --resolution {text!r}; expected WxH like 1280x720")


def apply_overrides(config: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    cfg = dict(config)
    if args.storage:
        cfg["storage_path"] = args.storage
    if args.renderer:
        cfg["renderer"] = args.renderer
    if args.engine:
        cfg["blender_engine"] = args.engine
    if args.fps:
        cfg["render_fps"] = args.fps
    if args.resolution:
        cfg["render_width"], cfg["render_height"] = _parse_resolution(args.resolution)
    if args.dummy:
        cfg["text_provider"] = cfg["vision_provider"] = cfg["image_provider"] = "dummy"
        cfg.setdefault("speech_provider", "dummy")
        if cfg["speech_provider"] == "auto":
            cfg["speech_provider"] = "dummy"
    if getattr(args, "characters", None):
        cfg["blender_characters"] = args.characters
    if getattr(args, "model", None):
        models = dict(cfg.get("character_models") or {})
        for item in args.model:
            name, sep, path = item.partition("=")
            if not sep or not name.strip() or not path.strip():
                raise argparse.ArgumentTypeError(f"invalid --model {item!r}; expected NAME=FILE.glb")
            if not Path(path.strip()).is_file():
                raise argparse.ArgumentTypeError(f"--model file not found: {path.strip()}")
            models[name.strip()] = path.strip()
        cfg["character_models"] = models
        cfg["blender_characters"] = "rigged"
    if getattr(args, "speech", None):
        cfg["speech_provider"] = args.speech
    if getattr(args, "voice", None):
        voices = dict(cfg.get("voices") or {})
        for item in args.voice:
            name, sep, voice = item.partition("=")
            if not sep or not name.strip() or not voice.strip():
                raise argparse.ArgumentTypeError(f"invalid --voice {item!r}; expected NAME=VOICE")
            voices[name.strip()] = voice.strip()
        cfg["voices"] = voices
    if args.validate:
        cfg["skip_validation"] = False
    return cfg


# ----------------------------------------------------------------------
# run
# ----------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    prompt = args.prompt
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"error: cannot read prompt file: {e}", file=sys.stderr)
            return EXIT_USAGE
    if not prompt or not prompt.strip():
        print("error: give a prompt (positional argument or --prompt-file)", file=sys.stderr)
        return EXIT_USAGE

    try:
        config = apply_overrides(load_config(args.config), args)
    except argparse.ArgumentTypeError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE

    from src.integrations.ffmpeg.video_assembler import FFmpegError, VideoAssembler
    from src.integrations.otio.timeline_export import TimelineExportError, export_timeline

    started = time.time()
    summary: Dict[str, Any] = {"prompt": prompt.strip(), "shots": [], "failures": []}

    def say(msg: str) -> None:
        if not args.json:
            print(msg, flush=True)

    try:
        ensure_storage(config)
        director, orchestrator, world_state, _assets = build_pipeline(config)
    except Exception as e:  # noqa: BLE001 - surface any wiring/config problem clearly
        print(f"error: could not start the pipeline: {e}", file=sys.stderr)
        return EXIT_FAILED

    try:
        say("Planning shots…")
        plan = director.generate_production_plan(prompt)
        tasks = director.create_task_schedule(plan)
        say(f"  {len(plan.shots)} shots, {len(tasks)} tasks")

        orchestrator.on_shot_update = lambda sid, status, detail: say(f"  [{sid}] {status}")
        orchestrator.on_progress = lambda i, n: None
        orchestrator.load_task_schedule(tasks)
        orchestrator.run_pipeline()
    except Exception as e:  # noqa: BLE001
        print(f"error: pipeline failed: {e}", file=sys.stderr)
        return EXIT_FAILED

    rendered = orchestrator.rendered_frames
    # Final cut follows the Director's plan order (not database order).
    ordered = {s.shot_id: rendered[s.shot_id] for s in plan.shots if s.shot_id in rendered}
    summary["shots"] = [s.shot_id for s in plan.shots]
    summary["failures"] = [
        {"shot": f.shot_id, "stage": f.task_type, "error": str(f.error)} for f in orchestrator.failures
    ]

    if not ordered:
        _emit(summary, args, started, video=None, timeline=None)
        print("error: no shot rendered successfully", file=sys.stderr)
        for f in summary["failures"]:
            print(f"  - {f['shot']} ({f['stage']}): {f['error']}", file=sys.stderr)
        return EXIT_FAILED

    assembler = VideoAssembler(config)
    try:
        say("Assembling video…")
        audio = {sid: p for sid, p in orchestrator.shot_audio.items() if sid in ordered}
        video = assembler.assemble(ordered, output_name=args.output, shot_audio=audio or None)
        summary["audio_shots"] = sorted(audio)
    except FFmpegError as e:
        print(f"error: video assembly failed: {e}", file=sys.stderr)
        return EXIT_FAILED

    timeline = None
    if not args.no_timeline:
        try:
            timeline = export_timeline(
                timeline_shots(plan, ordered, assembler.fps, world_state), assembler.segment_paths,
                Path(video).with_suffix(".otio"), fps=assembler.fps, shot_audio=audio or None,
            )
        except TimelineExportError as e:
            print(f"warning: timeline not written: {e}", file=sys.stderr)

    _emit(summary, args, started, video=video, timeline=timeline)
    return EXIT_PARTIAL if summary["failures"] else EXIT_OK


def _emit(summary, args, started, video, timeline) -> None:
    summary["video"] = str(video) if video else None
    summary["timeline"] = str(timeline) if timeline else None
    summary["seconds"] = round(time.time() - started, 1)
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    if video:
        print(f"\nVideo:    {video}")
    if timeline:
        print(f"Timeline: {timeline}")
    if summary["failures"]:
        print(f"\n{len(summary['failures'])} problem(s):")
        for f in summary["failures"]:
            print(f"  - {f['shot']} ({f['stage']}): {f['error']}")
    print(f"Done in {summary['seconds']}s")


# ----------------------------------------------------------------------
# doctor
# ----------------------------------------------------------------------

OK, WARN, FAIL = "ok", "warn", "FAIL"


def run_checks(config: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Return [(status, name, detail)]. FAIL means BlazEng cannot run at all."""
    checks: List[Tuple[str, str, str]] = []

    v = sys.version_info
    checks.append((OK if v >= (3, 11) else FAIL, "Python", f"{platform.python_version()} (need 3.11+)"))

    for module, pip_name, required in [
        ("yaml", "pyyaml", True), ("pydantic", "pydantic", True), ("PIL", "pillow", True),
        ("opentimelineio", "opentimelineio", True), ("PyQt6", "'blazeng[ui]'", False),
    ]:
        try:
            __import__(module)
            checks.append((OK, module, "installed"))
        except ImportError:
            what = "required" if required else "optional — only the desktop app needs it; the CLI runs without"
            checks.append((FAIL if required else WARN, module, f"missing — pip install {pip_name} [{what}]"))

    ffmpeg = shutil.which(str(config.get("ffmpeg_path", "ffmpeg")))
    checks.append((OK if ffmpeg else FAIL, "ffmpeg", ffmpeg or "not found — required to assemble video"))
    xvfb = shutil.which("xvfb-run")
    on_linux = platform.system() == "Linux"
    checks.append((OK if xvfb else (WARN if on_linux else OK), "xvfb-run",
                   xvfb or ("not found — needed for headless rendering (sudo apt install xvfb)"
                            if on_linux else "not needed on this OS")))

    godot = Path(str(config.get("godot_binary_path", "./bin/godot")))
    blender = shutil.which(str(config.get("blender_path", "blender")))
    checks.append((OK if godot.exists() else WARN, "godot", str(godot) if godot.exists() else f"not found at {godot}"))
    checks.append((OK if blender else WARN, "blender", blender or "not found on PATH"))
    if not godot.exists() and not blender:
        checks.append((FAIL, "renderer", "neither Godot nor Blender found — install one"))

    speech = str(config.get("speech_provider", "auto")).lower()
    espeak = shutil.which(str(config.get("espeak_path", "espeak-ng")))
    if speech in ("auto", "espeak"):
        checks.append((OK if espeak else WARN, "speech",
                       f"espeak-ng at {espeak}" if espeak else
                       "espeak-ng not found — dialogue will be silent (sudo apt install espeak-ng)"))
    elif speech == "piper":
        from src.providers.speech_providers import piper_voices_dir
        vdir = piper_voices_dir(config)
        have = sorted(p.stem for p in vdir.glob("*.onnx")) if vdir.is_dir() else []
        piper = shutil.which(str(config.get("piper_path", "piper")))
        status = OK if (piper and have) else WARN
        checks.append((status, "speech", f"piper {'found' if piper else 'NOT found (pip install piper-tts)'}; "
                                         f"{len(have)} voice(s) in {vdir}"))
    elif speech == "command":
        cmd = config.get("speech_command") or []
        found = bool(cmd) and (shutil.which(str(cmd[0])) or Path(str(cmd[0])).exists())
        checks.append((OK if found else WARN, "speech", f"speech_command: {cmd[0] if cmd else '(not set)'}"
                                                         + ("" if found else " — program not found")))
    else:
        checks.append((OK, "speech", f"speech_provider: {speech}"))

    try:
        from src.providers.provider_factory import validate_provider_config
        valid, msg = validate_provider_config(config)
        checks.append((OK if valid else WARN, "providers", "configured" if valid else str(msg)))
    except Exception as e:  # noqa: BLE001
        checks.append((WARN, "providers", f"could not validate: {e}"))
    return checks


def cmd_voices(args: argparse.Namespace) -> int:
    from src.providers.speech_providers import get_speech_provider

    config = load_config(args.config)
    if args.speech:
        config["speech_provider"] = args.speech
    try:
        provider = get_speech_provider(config)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAILED
    if provider is None:
        print("No speech provider is active (speech_provider: none, or auto found nothing installed).")
        return EXIT_OK
    pool = provider.voices()
    print(f"Speech provider: {type(provider).__name__}")
    print("Voices: " + (", ".join(pool) if pool else "(single default voice)"))
    if provider.assignments:
        print("Assigned: " + ", ".join(f"{k} -> {v}" for k, v in sorted(provider.assignments.items())))
    if type(provider).__name__ == "PiperSpeechProvider" and not pool:
        print(f"Put <name>.onnx (+ <name>.onnx.json) Piper voice files in {provider.voices_dir}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    checks = run_checks(config)
    width = max(len(n) for _, n, _ in checks)
    for status, name, detail in checks:
        print(f"[{status:>4}] {name:<{width}}  {detail}")
    failed = [c for c in checks if c[0] == FAIL]
    print("\n" + ("Ready." if not failed else f"{len(failed)} blocking problem(s) — fix the FAIL lines above."))
    return EXIT_OK if not failed else EXIT_FAILED


# ----------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "version":
        print(f"blazeng {__version__}")
        return EXIT_OK
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "voices":
        return cmd_voices(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
