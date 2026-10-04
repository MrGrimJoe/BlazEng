import shutil
import subprocess

import pytest

from src.integrations.ffmpeg.video_assembler import FFmpegError, VideoAssembler, _sanitize


def _make_frames(tmp_path, shot_id, count, size="64x48", color="red"):
    frames_dir = tmp_path / "frames" / shot_id
    frames_dir.mkdir(parents=True)
    frames = []
    for i in range(count):
        f = frames_dir / f"frame{i:08d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d=1",
             "-frames:v", "1", str(f)],
            capture_output=True, timeout=30, check=True,
        )
        frames.append(f)
    return frames


HAS_FFMPEG = shutil.which("ffmpeg") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.fixture
def assembler(tmp_path):
    return VideoAssembler({"storage_path": str(tmp_path / "storage"), "render_fps": 24})


class TestSanitize:
    def test_normal_name(self):
        assert _sanitize("shot_001") == "shot_001"

    def test_special_chars(self):
        assert _sanitize("shot/001") == "shot_001"


class TestAssembleValidation:
    def test_missing_ffmpeg_raises(self, tmp_path):
        assembler = VideoAssembler({
            "storage_path": str(tmp_path / "storage"),
            "ffmpeg_path": "not_a_real_binary_xyz",
        })
        with pytest.raises(FFmpegError, match="not found"):
            assembler.assemble({"shot_001": [tmp_path / "f.png"]})

    def test_empty_shot_frames_raises(self, assembler):
        with pytest.raises(FFmpegError, match="No shots"):
            assembler.assemble({})

    def test_shot_with_no_frames_raises(self, assembler, tmp_path):
        with pytest.raises(FFmpegError, match="no frames"):
            assembler.assemble({"shot_001": []})

    def test_frame_pattern_requires_trailing_digits(self, assembler, tmp_path):
        bad_frame = tmp_path / "no_digits.png"
        bad_frame.write_bytes(b"fake")
        with pytest.raises(FFmpegError, match="trailing digits"):
            assembler.assemble({"shot_001": [bad_frame]})


@requires_ffmpeg
class TestAssembleRealEncoding:
    def test_single_shot_produces_valid_mp4(self, assembler, tmp_path):
        frames = _make_frames(tmp_path, "shot_001", 5)
        output = assembler.assemble({"shot_001": frames})

        assert output.exists()
        assert output.stat().st_size > 0
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
             "-of", "csv=p=0", str(output)],
            capture_output=True, text=True, timeout=30,
        )
        assert probe.stdout.strip() == "h264"

    def test_multi_shot_concatenates_in_order(self, assembler, tmp_path):
        frames1 = _make_frames(tmp_path, "shot_001", 4, color="red")
        frames2 = _make_frames(tmp_path, "shot_002", 4, color="blue")
        output = assembler.assemble({"shot_001": frames1, "shot_002": frames2})

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(output)],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip())
        # 8 total frames at 24fps ≈ 0.33s
        assert 0.2 < duration < 0.6

    def test_output_written_to_storage_output_dir(self, assembler, tmp_path):
        frames = _make_frames(tmp_path, "shot_001", 3)
        output = assembler.assemble({"shot_001": frames})
        assert output.parent == assembler.output_dir

    def test_custom_output_name_respected(self, assembler, tmp_path):
        frames = _make_frames(tmp_path, "shot_001", 3)
        output = assembler.assemble({"shot_001": frames}, output_name="my_video.mp4")
        assert output.name == "my_video.mp4"


class TestBlenderStyleFrames:
    """Blender numbers frames from 1 (Godot from 0) and users may pick odd sizes."""

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
    def test_one_based_frames_and_odd_dimensions(self, tmp_path):
        from PIL import Image

        frames = []
        for i in range(1, 7):
            p = tmp_path / f"frame{i:04d}.png"
            Image.new("RGB", (63, 35), (i * 20, 50, 90)).save(p)  # odd size on purpose
            frames.append(p)
        asm = VideoAssembler({"storage_path": str(tmp_path / "s"), "render_fps": 6})
        out = asm.assemble({"s1": frames})
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames,width,height", "-of", "csv=p=0", str(out)],
            capture_output=True, text=True,
        ).stdout.strip()
        w, h, n = probe.split(",")
        assert int(n) == 6          # no frame dropped by a wrong start number
        assert int(w) % 2 == 0 and int(h) % 2 == 0
        assert asm.segment_paths["s1"].exists()
