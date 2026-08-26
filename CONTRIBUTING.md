# Contributing to BlazEng

Thanks for interest in building this out! Priority 1 (core logic) is done —
see below. We need help with Priority 2 onward: rendering, validation,
export, and UI. Here's how to contribute.

---

## What Needs Implementation

### Priority 1: Core Logic — ✅ DONE
These used to block everything else — they don't anymore.

- **WorldStateManager** (`src/core/world_state/world_state.py`) — ✅ implemented,
  16 tests, 98% coverage. SQLite-backed, with a rollback-on-failure test proving
  transactional integrity.
- **Director** (`src/core/director/director.py`) — ✅ implemented, 23 tests,
  100% coverage. LLM prompt → JSON shot plan, with retry-on-unparseable-output
  and a working `repair_shot()`.
- **AssetManager** (`src/core/asset_manager/asset_manager.py`) — ✅ implemented,
  17 tests, 100% coverage. Caching/versioning keyed on description match, with
  corrupted-cache and missing-file recovery tested.

**Read these three files before starting Priority 2+** — they're the
reference implementation for this project's style: dataclasses for
structured data, defensive parsing of anything an LLM returns, tests that
exercise real failure modes (corrupted files, rollback, malformed input)
rather than just the happy path. `tests/test_integration_pipeline.py` shows
how to wire a new module into an end-to-end test using the dummy providers
in `src/providers/dummy_provider.py`.

**Known gap**: the Gemini provider classes (`src/providers/gemini_provider.py`)
are implemented and unit-tested against a mocked SDK client, but have never
been called against the live Gemini API in development — the sandbox this
was built in blocks that network host. If you have a real API key, running
one live call against each of `GeminiTextProvider`, `GeminiVisionProvider`,
and `GeminiImageProvider` and confirming the response shape matches what the
code expects would be a genuinely useful first contribution.

### Priority 2: Pipeline Processing ⭐⭐ ← Start here
These depend on Priority 1.

- **SceneComposer** (`src/core/scene_composer/scene_composer.py`)
  - Build Godot scene JSON/tscn from shot metadata
  - Position assets, set lighting, camera
  - ~200 lines

- **ValidatorManager** (`src/core/validator/validator_manager.py`)
  - Implement 4 validators (script, visual, character, temporal)
  - Response matching logic (fuzzy matching for small model outputs)
  - ~250 lines

- **RepairEngine** (`src/core/repair/repair_engine.py`)
  - Failure analysis
  - Targeted regeneration strategy
  - Retry logic (max 3 attempts)
  - ~150 lines

### Priority 3: Integrations ⭐
These unblock rendering.

- **Godot Rendering Bridge** (`src/integrations/godot/renderer.py`)
  - Headless Godot invocation
  - Frame sequence output
  - ~100 lines

- **FFmpeg Integration** (`src/integrations/ffmpeg/video_assembly.py`)
  - Frame sequence → MP4
  - OpenTimelineIO export
  - ~80 lines

### Priority 4: UI ⭐
These enhance usability.

- **Timeline Widget** (`src/ui/right_panel/timeline.py`)
  - Shot visualization
  - Click-to-select
  - Status indicators
  - ~150 lines

- **Asset Browser** (`src/ui/left_panel/asset_browser.py`)
  - Tree view of assets
  - Version comparison
  - ~120 lines

- **World State Viewer** (`src/ui/left_panel/world_state_viewer.py`)
  - Character status display
  - Prop tracking
  - ~100 lines

---

## How to Contribute

### 1. Pick a Module

Choose from the list above. Priority 1 modules unblock everything, so those are most helpful right now.

Check `ARCHITECTURE.md` for detailed design of each module.

### 2. Fork & Clone

```bash
git clone https://github.com/YOUR_USERNAME/BlazEng.git
cd BlazEng
git checkout -b feature/implement-world-state
```

### 3. Implement

Follow the interface in the stub file. Example for WorldStateManager:

```python
# src/core/world_state/world_state.py
# Replace the stub with your implementation

import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any

class WorldStateManager:
    def __init__(self, config: Dict[str, Any]):
        """Initialize SQLite database."""
        self.storage_path = Path(config.get("storage_path", "./storage"))
        self.db_path = self.storage_path / "database" / "world_state.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
    
    def _init_schema(self):
        """Create tables if they don't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                first_appearance_shot TEXT,
                appearance_description TEXT,
                version INTEGER DEFAULT 1
            )
        """)
        # ... more tables ...
        self.conn.commit()
    
    def add_character(self, name: str, metadata: Dict[str, Any]) -> None:
        """Register a character."""
        self.conn.execute(
            "INSERT INTO characters (name, appearance_description) VALUES (?, ?)",
            (name, metadata.get("appearance", ""))
        )
        self.conn.commit()
    
    # ... implement other methods ...
```

### 4. Write Tests

```python
# tests/test_world_state.py
import pytest
from pathlib import Path
from src.core.world_state.world_state import WorldStateManager

@pytest.fixture
def world_state(tmp_path):
    """Create a test WorldStateManager with temp storage."""
    config = {"storage_path": str(tmp_path / "storage")}
    return WorldStateManager(config)

def test_add_character(world_state):
    """Test adding a character."""
    world_state.add_character("detective", {
        "appearance": "tall, brown fedora, trench coat"
    })
    
    char = world_state.get_character("detective")
    assert char is not None
    assert "fedora" in char["appearance"]

def test_character_state_update(world_state):
    """Test updating character state (e.g., gets injured)."""
    world_state.add_character("detective", {"appearance": "healthy"})
    world_state.update_character("detective", {"injuries": "bullet wound left arm"})
    
    char = world_state.get_character("detective")
    assert "bullet wound" in char.get("injuries", "")
```

Run tests:
```bash
pytest tests/test_world_state.py -v
```

### 5. Submit a PR

```bash
git add src/core/world_state/world_state.py tests/test_world_state.py
git commit -m "Implement WorldStateManager with SQLite backend"
git push origin feature/implement-world-state
```

Go to GitHub and open a PR. Include:
- What module you implemented
- How you tested it
- Any design decisions you made
- Any open questions

---

## Code Standards

### Style
- Follow PEP 8
- Use type hints: `def get_asset(self, name: str) -> Optional[Path]:`
- Docstrings for all classes and public methods

```python
class Director:
    """Parse user prompts into shot plans."""
    
    def generate_production_plan(self, prompt: str) -> ProductionPlan:
        """Break narrative into individual shots.
        
        Args:
            prompt: User's story description
        
        Returns:
            ProductionPlan with list of Shot objects, each containing:
            - shot_id, scene_description, characters, camera_angle,
              lighting, action, expected_duration
        
        Raises:
            ValueError: If prompt is empty
            RuntimeError: If LLM call fails
        """
```

### Logging
Use the module's logger:
```python
import logging
logger = logging.getLogger(__name__)

def do_something():
    logger.info("Processing shot 1")
    logger.debug(f"Asset path: {path}")
    logger.error("Failed to generate asset", exc_info=True)
```

### Error Handling
Catch specific exceptions, log, and reraise or handle gracefully:
```python
try:
    result = self.text_provider.generate(prompt)
except ValueError as e:
    logger.error(f"Invalid prompt: {e}")
    raise
except Exception as e:
    logger.error(f"LLM call failed: {e}", exc_info=True)
    # Maybe retry or return default?
```

### Testing
- Write unit tests for all logic
- Use pytest fixtures for setup
- Aim for >80% coverage (check with `pytest --cov`)

```bash
pytest tests/ -v --cov=src --cov-report=html
```

---

## Design Discussions

For complex decisions, open an issue before implementing:
- "How should we handle character appearance versioning?"
- "Should asset caching be per-project or global?"
- "What happens if LLM call times out mid-repair?"

Ask in the issue. The community can help!

---

## Review Process

When you submit a PR:

1. **Automated checks run** (linting, type checking, tests)
2. **Code review** — someone reads your code
3. **Feedback** — we might suggest changes
4. **Iteration** — you update based on feedback
5. **Merge** — once approved, your code goes live!

Be patient, we're volunteers. Reviews might take a few days.

---

## Debugging Tips

### Run with logging
```bash
PYTHONUNBUFFERED=1 python main.py 2>&1 | tee debug.log
```

### Test a single function
```bash
pytest tests/test_world_state.py::test_add_character -vv
```

### Print debug info
```python
print(f"DEBUG: {var}")  # OK for debugging
# Always remove before PR!
```

### Use pdb
```python
import pdb; pdb.set_trace()  # Debugger will pause here
# Commands: n (next), s (step), c (continue), p var (print)
```

---

## Getting Help

- **Architecture questions**: Read `ARCHITECTURE.md`
- **Design questions**: Open a GitHub issue
- **Implementation questions**: Comment on an open PR
- **Stuck on a bug**: Describe the issue with stack trace in an issue

---

## What Happens After You Contribute

- ✅ Your code gets merged
- ✅ You're listed in `CONTRIBUTORS.md`
- ✅ The pipeline gets stronger
- ✅ Your work helps people make videos!

---

## Examples

### Example 1: Implement a simple provider

```python
# src/providers/dummy_provider.py
class DummyTextProvider:
    """For testing without API calls."""
    
    def generate(self, prompt: str) -> str:
        """Return a fake response."""
        return f"[DUMMY RESPONSE TO: {prompt}]"

# In provider_factory.py, add:
elif config['text_provider'] == 'dummy':
    return DummyTextProvider()
```

Then test:
```python
def test_with_dummy_provider():
    config = {"text_provider": "dummy"}
    director = Director(DummyTextProvider(), world_state)
    plan = director.generate_production_plan("test prompt")
    assert plan is not None
```

### Example 2: Implement asset versioning

```python
def get_asset(self, asset_type: str, name: str, description: str, 
              force_regenerate=False) -> Path:
    """Get or generate asset with versioning."""
    
    # Check cache
    asset_dir = self.storage_path / "assets" / asset_type / name
    if asset_dir.exists() and not force_regenerate:
        # Find latest version
        versions = sorted(asset_dir.glob("v*"), key=lambda p: int(p.name[1:]))
        if versions:
            latest = versions[-1]
            pngs = list(latest.glob("*.png"))
            if pngs:
                logger.info(f"Cache hit: {pngs[0]}")
                return pngs[0]
    
    # Generate new version
    version = 1
    if asset_dir.exists():
        versions = list(asset_dir.glob("v*"))
        version = max(int(v.name[1:]) for v in versions) + 1
    
    asset_path = asset_dir / f"v{version}"
    asset_path.mkdir(parents=True, exist_ok=True)
    
    # Call provider
    image = self.image_provider.generate(description)
    png_path = asset_path / f"{name}_v{version}.png"
    image.save(png_path)
    
    # Log what was used
    (asset_path / "reference.txt").write_text(f"Prompt: {description}")
    
    logger.info(f"Generated: {png_path} (v{version})")
    return png_path
```

---

**Happy contributing!**

Let's build something amazing together.

---

*Last updated: August 23, 2026*
