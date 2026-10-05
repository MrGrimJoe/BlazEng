"""
PipelineWorker — runs a whole production off the GUI thread.

The worker builds its OWN pipeline (providers, world state, renderer) inside
its thread: SQLite connections can't be shared across threads, and building
providers/loading models can take seconds, which must never freeze the UI.
It talks to the GUI exclusively through signals (queued across threads), and
never touches a widget.
"""

import logging
from pathlib import Path
from typing import Any, Dict

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class PipelineWorker(QObject):
    plan_ready = pyqtSignal(list)            # [{shot_id, duration, scene_description, ...}]
    shot_update = pyqtSignal(str, str, str)  # shot_id, status, detail
    progress = pyqtSignal(int, int)          # done, total
    stage = pyqtSignal(str)                  # human-readable stage message
    finished = pyqtSignal(dict)              # summary (always emitted exactly once)
    failed = pyqtSignal(str)                 # fatal error message (then finished is NOT emitted)

    def __init__(self, config: Dict[str, Any], prompt: str, output_name: str = "final.mp4"):
        super().__init__()
        self.config = dict(config)
        self.prompt = prompt
        self.output_name = output_name
        self._orchestrator = None
        self._cancel = False

    def cancel(self) -> None:
        """Thread-safe: callable from the GUI thread while run() is executing."""
        self._cancel = True
        orch = self._orchestrator
        if orch is not None:
            orch.cancel()

    @pyqtSlot()
    def run(self) -> None:
        from src.integrations.ffmpeg.video_assembler import FFmpegError, VideoAssembler
        from src.integrations.otio.timeline_export import TimelineExportError, export_timeline
        from src.pipeline import build_pipeline, ensure_storage, timeline_shots

        try:
            self.stage.emit("Starting pipeline…")
            ensure_storage(self.config)
            director, orchestrator, world_state, _assets = build_pipeline(self.config)
            self._orchestrator = orchestrator
            if self._cancel:
                self.finished.emit(self._summary(cancelled=True))
                return

            self.stage.emit("Planning shots…")
            plan = director.generate_production_plan(self.prompt)
            tasks = director.create_task_schedule(plan)
            self.plan_ready.emit([
                {"shot_id": s.shot_id, "duration": s.duration_seconds,
                 "scene_description": s.scene_description, "characters": list(s.characters),
                 "camera_angle": s.camera_angle, "lighting": s.lighting, "action": s.action}
                for s in plan.shots
            ])

            orchestrator.on_shot_update = lambda sid, status, detail: self.shot_update.emit(sid, status, detail)
            orchestrator.on_progress = lambda done, total: self.progress.emit(done, total)
            orchestrator.load_task_schedule(tasks)
            self.stage.emit("Generating, composing and rendering…")
            orchestrator.run_pipeline()

            rendered = orchestrator.rendered_frames
            ordered = {s.shot_id: rendered[s.shot_id] for s in plan.shots if s.shot_id in rendered}
            failures = [{"shot": f.shot_id, "stage": f.task_type, "error": str(f.error)}
                        for f in orchestrator.failures]
            for f in orchestrator.failures:
                self.shot_update.emit(f.shot_id, "failed", str(f.error))

            video = timeline = None
            if orchestrator.cancelled:
                self.finished.emit(self._summary(cancelled=True, frames=ordered, failures=failures))
                return
            if ordered:
                self.stage.emit("Assembling video…")
                assembler = VideoAssembler(self.config)
                audio = {sid: p for sid, p in orchestrator.shot_audio.items() if sid in ordered}
                video = assembler.assemble(ordered, output_name=self.output_name, shot_audio=audio or None)
                try:
                    timeline = export_timeline(
                        timeline_shots(plan, ordered, assembler.fps, world_state),
                        assembler.segment_paths, Path(video).with_suffix(".otio"),
                        fps=assembler.fps, shot_audio=audio or None)
                except TimelineExportError as e:
                    logger.warning(f"Timeline not written: {e}")
            self.finished.emit(self._summary(video=video, timeline=timeline, frames=ordered, failures=failures))
        except (FFmpegError, Exception) as e:  # noqa: BLE001 - everything must reach the GUI as a message
            logger.error(f"Pipeline worker failed: {e}", exc_info=True)
            self.failed.emit(str(e))

    @staticmethod
    def _summary(video=None, timeline=None, frames=None, failures=None, cancelled=False) -> Dict[str, Any]:
        return {
            "video": str(video) if video else None,
            "timeline": str(timeline) if timeline else None,
            "frames": {k: [str(p) for p in v] for k, v in (frames or {}).items()},
            "failures": failures or [],
            "cancelled": cancelled,
        }
