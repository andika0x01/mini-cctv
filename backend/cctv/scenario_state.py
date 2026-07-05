import sqlite3
from pathlib import Path
from threading import Lock


class ScenarioStateStore:
    def __init__(self, db_path: str):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_state (
                scenario_key TEXT PRIMARY KEY,
                is_active INTEGER NOT NULL,
                last_changed_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()
        self._lock = Lock()

    def is_active(self, scenario_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT is_active FROM scenario_state WHERE scenario_key = ?",
                (scenario_key,),
            ).fetchone()
        if row is None:
            return False
        return bool(row[0])

    def set_active(self, scenario_key: str, active: bool, changed_at: float) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO scenario_state (scenario_key, is_active, last_changed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scenario_key) DO UPDATE SET
                    is_active = excluded.is_active,
                    last_changed_at = excluded.last_changed_at
                """,
                (scenario_key, int(active), changed_at),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

