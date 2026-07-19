"""
AI Production Studio — One-click setup.
Installs Python deps, downloads Godot + FFmpeg, configures model, writes config.yaml.
"""
import os, sys, platform, subprocess, zipfile, tarfile, shutil, urllib.request
from pathlib import Path

SYSTEM = platform.system()
ROOT   = Path(__file__).parent
BIN    = ROOT / "bin"

GODOT_URLS = {
    "Windows": "https://github.com/godotengine/godot/releases/download/4.2.1-stable/Godot_v4.2.1-stable_win64.exe.zip",
    "Darwin":  "https://github.com/godotengine/godot/releases/download/4.2.1-stable/Godot_v4.2.1-stable_macos.universal.zip",
    "Linux":   "https://github.com/godotengine/godot/releases/download/4.2.1-stable/Godot_v4.2.1-stable_linux.x86_64.zip",
}
FFMPEG_URLS = {
    "Windows": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "Darwin":  "https://evermeet.cx/ffmpeg/ffmpeg-6.1.zip",
    "Linux":   "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
}


def banner(msg): print(f"\n{'='*60}\n  {msg}\n{'='*60}")
def run(cmd): subprocess.run(cmd, check=True)
def download(url, dest):
    print(f"  Downloading {Path(dest).name}...")
    urllib.request.urlretrieve(url, dest)
    print(f"  ✅ {dest}")


def check_python():
    banner("Python version")
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 11):
        print(f"  ❌ Python 3.11+ required (you have {v.major}.{v.minor})")
        sys.exit(1)
    print(f"  ✅ Python {v.major}.{v.minor}.{v.micro}")


def install_core_deps():
    banner("Installing core Python dependencies")
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "-q"])
    # Install only core deps — heavy optional ones are separate
    core = ["pyyaml>=6.0.1", "pydantic>=2.5.3", "PyQt6>=6.6.1",
            "requests>=2.31.0", "google-genai>=1.0.0", "huggingface_hub>=0.20.3"]
    run([sys.executable, "-m", "pip", "install"] + core + ["-q"])
    print("  ✅ Core deps installed")


def ask_gpu():
    banner("GPU / CPU selection for local models")
    print("  If you plan to use local AI models (HuggingFace / Stable Diffusion),")
    print("  choose your hardware:")
    print("  1) I have an NVIDIA GPU  — installs torch with CUDA (large download)")
    print("  2) CPU only              — installs torch CPU build (smaller, slower)")
    print("  3) Skip                  — I will only use cloud models (Gemini)")
    choice = input("  Enter 1, 2, or 3: ").strip()
    if choice == "1":
        run([sys.executable, "-m", "pip", "install",
             "torch", "transformers", "accelerate", "sentencepiece", "diffusers", "-q"])
        print("  ✅ GPU torch stack installed")
    elif choice == "2":
        run([sys.executable, "-m", "pip", "install",
             "torch", "--index-url", "https://download.pytorch.org/whl/cpu",
             "-q"])
        run([sys.executable, "-m", "pip", "install",
             "transformers", "accelerate", "sentencepiece", "diffusers", "-q"])
        print("  ✅ CPU torch stack installed")
    else:
        print("  ✅ Skipped — using cloud models only")


def install_godot():
    banner("Installing Godot 4")
    BIN.mkdir(exist_ok=True)
    url = GODOT_URLS.get(SYSTEM)
    if not url: print("  ⚠️  Unsupported OS — install Godot manually"); return ""
    zp = BIN / "godot.zip"
    download(url, zp)
    with zipfile.ZipFile(zp) as z: z.extractall(BIN / "godot_ext")
    zp.unlink()
    for p in (BIN / "godot_ext").rglob("*"):
        if "godot" in p.name.lower() and p.suffix in (".exe", "") and p.is_file():
            dest = BIN / ("godot.exe" if SYSTEM == "Windows" else "godot")
            shutil.copy2(p, dest)
            if SYSTEM != "Windows": dest.chmod(0o755)
            shutil.rmtree(BIN / "godot_ext")
            print(f"  ✅ Godot: {dest}"); return str(dest)
    print("  ⚠️  Could not locate Godot binary — install manually"); return ""


def install_ffmpeg():
    banner("Installing FFmpeg")
    BIN.mkdir(exist_ok=True)
    url = FFMPEG_URLS.get(SYSTEM)
    if not url: print("  ⚠️  Unsupported OS — install FFmpeg manually"); return ""
    archive = BIN / ("ffmpeg.zip" if "zip" in url else "ffmpeg.tar.xz")
    download(url, archive)
    if str(archive).endswith(".zip"):
        with zipfile.ZipFile(archive) as z: z.extractall(BIN / "ffmpeg_ext")
    else:
        with tarfile.open(archive) as t: t.extractall(BIN / "ffmpeg_ext")
    archive.unlink()
    for p in (BIN / "ffmpeg_ext").rglob("*"):
        if p.stem == "ffmpeg" and p.is_file():
            dest = BIN / ("ffmpeg.exe" if SYSTEM == "Windows" else "ffmpeg")
            shutil.copy2(p, dest)
            if SYSTEM != "Windows": dest.chmod(0o755)
            shutil.rmtree(BIN / "ffmpeg_ext")
            print(f"  ✅ FFmpeg: {dest}"); return str(dest)
    print("  ⚠️  Could not locate FFmpeg binary — install manually"); return ""


def choose_model():
    banner("Choose your AI model")
    print("  1) Cloud  — Gemini via Google AI Studio (needs API key + internet)")
    print("  2) Local  — HuggingFace model (runs on your machine, no API key needed)")
    choice = input("  Enter 1 or 2: ").strip()
    if choice == "2":
        print("\n  Browse: huggingface.co/models?pipeline_tag=text-generation")
        repo   = input("  Text model repo ID (e.g. Qwen/Qwen2.5-3B-Instruct): ").strip()
        img_r  = input("  Image model repo ID (e.g. stabilityai/sdxl-turbo) or blank to skip: ").strip()
        gkey   = input("  Gemini key for vision validation (optional, or blank): ").strip()
        return {
            "text_provider":   "huggingface",
            "vision_provider": "gemini" if gkey else "huggingface",
            "image_provider":  "diffusers" if img_r else "imagen",
            "gemini_api_key":  gkey or "GEMINI_API_KEY_HERE",
            "hf_repo_id":      repo,
            "hf_image_repo_id": img_r,
        }
    else:
        print("\n  Get a free key: aistudio.google.com/app/apikey")
        key = input("  Gemini API key: ").strip()
        return {
            "text_provider":   "gemini",
            "vision_provider": "gemini",
            "image_provider":  "imagen",   # ← correct: uses ImagenProvider not GeminiProvider
            "gemini_api_key":  key,
        }


def write_config(godot_path, ffmpeg_path, model):
    banner("Writing config.yaml")
    cfg = f"""# AI Production Studio — Configuration
text_provider:   {model.get("text_provider",   "gemini")}
vision_provider: {model.get("vision_provider", "gemini")}
image_provider:  {model.get("image_provider",  "imagen")}

gemini_api_key:       {model.get("gemini_api_key", "GEMINI_API_KEY_HERE")}
vision_gemini_api_key: ""
image_api_key:         ""
gemini_model:         gemini-flash-latest
imagen_model:         imagen-3.0-generate-001

hf_repo_id:       {model.get("hf_repo_id",       "")}
hf_image_repo_id: {model.get("hf_image_repo_id", "")}
hf_device:        auto
hf_max_new_tokens: 1024

ollama_url:   http://localhost:11434
ollama_model: llama3

godot_path:   {godot_path  or "godot"}
blender_path: blender
ffmpeg_path:  {ffmpeg_path or "ffmpeg"}

storage_path:            ./storage
max_repair_attempts:     3
render_timeout_seconds:  300
render_fps:              24
render_width:            1920
render_height:           1080
"""
    (ROOT / "config.yaml").write_text(cfg)
    print("  ✅ config.yaml written")


def self_test(model):
    banner("Self-test")
    errors = []
    for pkg in ["PyQt6", "pydantic", "yaml", "requests"]:
        try:
            __import__(pkg.lower().replace("pyqt6", "PyQt6"))
            print(f"  ✅ {pkg}")
        except ImportError:
            print(f"  ❌ {pkg} missing"); errors.append(pkg)

    # Test Gemini if configured (uses new google-genai SDK)
    if model.get("text_provider") == "gemini":
        key = model.get("gemini_api_key", "")
        if key and key != "GEMINI_API_KEY_HERE":
            try:
                from google import genai
                client = genai.Client(api_key=key)
                r = client.models.generate_content(model="gemini-flash-latest", contents="Reply: OK")
                print("  ✅ Gemini API connected" if "OK" in r.text else "  ⚠️  Gemini: unexpected response")
            except Exception as e:
                print(f"  ❌ Gemini error: {e}"); errors.append("Gemini")
        else:
            print("  ⚠️  No Gemini key — skipping API test")

    # Test HuggingFace if configured (correct key: text_provider)
    if model.get("text_provider") == "huggingface":
        try:
            import huggingface_hub
            print(f"  ✅ huggingface_hub {huggingface_hub.__version__}")
            if model.get("hf_repo_id"):
                print(f"  ℹ️  Model {model['hf_repo_id']!r} will download on first launch")
        except ImportError as e:
            print(f"  ❌ {e}"); errors.append("huggingface_hub")

    if errors:
        print(f"\n  ⚠️  {len(errors)} issue(s): {errors}")
    else:
        print("\n  ✅ All checks passed")
    return len(errors) == 0


def main():
    banner("AI Production Studio — Setup")
    print(f"  System: {SYSTEM} | Python: {sys.version.split()[0]}")
    check_python()
    install_core_deps()
    ask_gpu()
    godot   = install_godot()
    ffmpeg  = install_ffmpeg()
    model   = choose_model()
    write_config(godot, ffmpeg, model)
    ok      = self_test(model)
    banner("Done")
    if ok:
        print("  python main.py")
    else:
        print("  Fix the issues above, then run: python main.py")


if __name__ == "__main__":
    main()
