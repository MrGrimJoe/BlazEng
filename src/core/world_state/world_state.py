"""
WorldStateManager — SQLite-backed continuity tracking.

Tracks characters, objects, world events, and shots so the pipeline can
keep appearance, props, and story state consistent across an entire
production without re-deriving it from scratch at every step.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WorldStateError(Exception):
    """Raised for invalid world-state operations (e.g. unknown character)."""


class WorldStateManager:
    """Manages persistent world state: characters, objects, events, shots.

    Backed by a single SQLite file. Every write commits immediately —
    this is a low-throughput, high-value-per-write workload (a handful
    of characters and dozens of shots per project), so simplicity beats
    batching here.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.storage_path = Path(config.get("storage_path", "./storage"))
        db_dir = self.storage_path / "database"
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_dir / "world_state.db"

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        logger.info(f"WorldStateManager ready — db: {self.db_path}")

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS characters (
                name TEXT PRIMARY KEY,
                first_appearance_shot TEXT,
                appearance TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS character_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_name TEXT NOT NULL REFERENCES characters(name),
                shot_id TEXT,
                clothing TEXT,
                injuries TEXT,
                props_json TEXT NOT NULL DEFAULT '[]',
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS objects (
                name TEXT PRIMARY KEY,
                description TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS world_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shot_id TEXT,
                description TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS shots (
                shot_id TEXT PRIMARY KEY,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_character_states_char
                ON character_states(character_name);
            CREATE INDEX IF NOT EXISTS idx_character_states_shot
                ON character_states(shot_id);
            """
        )
        self._conn.commit()

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Characters
    # ------------------------------------------------------------------

    def add_character(self, name: str, metadata: Dict[str, Any]) -> None:
        """Register a new character, or update metadata if it exists."""
        appearance = metadata.get("appearance", "")
        extra = {k: v for k, v in metadata.items() if k != "appearance"}
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO characters (name, appearance, metadata_json)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    appearance = excluded.appearance,
                    metadata_json = excluded.metadata_json,
                    version = version + 1
                """,
                (name, appearance, json.dumps(extra)),
            )
        logger.debug(f"Character upserted: {name}")

    def get_character(self, name: str) -> Optional[Dict[str, Any]]:
        """Return a character's current merged state, or None if unknown."""
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM characters WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None

        result = {
            "name": row["name"],
            "appearance": row["appearance"],
            "version": row["version"],
            **json.loads(row["metadata_json"]),
        }

        # Fold in the most recent recorded state (clothing/injuries/props),
        # if any exist, so callers get the latest continuity snapshot
        # without a second query.
        with self._cursor() as cur:
            latest = cur.execute(
                """
                SELECT clothing, injuries, props_json FROM character_states
                WHERE character_name = ?
                ORDER BY id DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
        if latest:
            if latest["clothing"]:
                result["clothing"] = latest["clothing"]
            if latest["injuries"]:
                result["injuries"] = latest["injuries"]
            props = json.loads(latest["props_json"])
            if props:
                result["props"] = props

        return result

    def update_character(self, name: str, updates: Dict[str, Any]) -> None:
        """Record a new state snapshot for a character (e.g. injury, new clothing).

        Raises WorldStateError if the character hasn't been registered yet —
        this is deliberate: an update to an unknown character usually means
        a typo or a missing add_character() call upstream, and failing loudly
        here is cheaper than silently tracking a phantom character.
        """
        if self.get_character(name) is None:
            raise WorldStateError(
                f"Cannot update unknown character '{name}' — call add_character() first"
            )
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO character_states
                    (character_name, shot_id, clothing, injuries, props_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name,
                    updates.get("shot_id"),
                    updates.get("clothing"),
                    updates.get("injuries"),
                    json.dumps(updates.get("props", [])),
                ),
            )
        logger.debug(f"Character state recorded: {name} -> {updates}")

    def list_characters(self) -> List[str]:
        with self._cursor() as cur:
            rows = cur.execute("SELECT name FROM characters ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    # ------------------------------------------------------------------
    # Objects
    # ------------------------------------------------------------------

    def add_object(self, name: str, metadata: Dict[str, Any]) -> None:
        description = metadata.get("description", "")
        extra = {k: v for k, v in metadata.items() if k != "description"}
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO objects (name, description, metadata_json)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    metadata_json = excluded.metadata_json,
                    version = version + 1
                """,
                (name, description, json.dumps(extra)),
            )

    def get_object(self, name: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            row = cur.execute("SELECT * FROM objects WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return {
            "name": row["name"],
            "description": row["description"],
            "version": row["version"],
            **json.loads(row["metadata_json"]),
        }

    # ------------------------------------------------------------------
    # World events
    # ------------------------------------------------------------------

    def add_event(self, description: str, shot_id: Optional[str] = None) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO world_events (shot_id, description) VALUES (?, ?)",
                (shot_id, description),
            )

    def get_events(self, shot_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            if shot_id is not None:
                rows = cur.execute(
                    "SELECT * FROM world_events WHERE shot_id = ? ORDER BY id",
                    (shot_id,),
                ).fetchall()
            else:
                rows = cur.execute("SELECT * FROM world_events ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Shots
    # ------------------------------------------------------------------

    def add_shot(self, shot_id: str, metadata: Dict[str, Any]) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO shots (shot_id, metadata_json) VALUES (?, ?)
                ON CONFLICT(shot_id) DO UPDATE SET metadata_json = excluded.metadata_json
                """,
                (shot_id, json.dumps(metadata)),
            )

    def get_shot(self, shot_id: str) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM shots WHERE shot_id = ?", (shot_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "shot_id": row["shot_id"],
            "status": row["status"],
            **json.loads(row["metadata_json"]),
        }

    def set_shot_status(self, shot_id: str, status: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE shots SET status = ? WHERE shot_id = ?", (status, shot_id)
            )

    def list_shots(self) -> List[Dict[str, Any]]:
        with self._cursor() as cur:
            rows = cur.execute("SELECT * FROM shots ORDER BY shot_id").fetchall()
        return [
            {"shot_id": r["shot_id"], "status": r["status"], **json.loads(r["metadata_json"])}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "WorldStateManager":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
