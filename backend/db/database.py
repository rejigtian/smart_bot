from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DB_PATH = Path(__file__).parent.parent / "data" / "db.sqlite3"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # ── Auto-migrate: add new columns to existing tables ─────────────
        # SQLite supports ADD COLUMN but not DROP/RENAME, so we just add
        # missing columns idempotently.

        async def _ensure_columns(table: str, columns: dict[str, str]):
            """Add missing columns to an existing table. columns = {name: DDL_type}"""
            existing = {
                row[1]
                for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            }
            for col, ddl in columns.items():
                if col not in existing:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

        await _ensure_columns("test_results", {
            "is_starred": "BOOLEAN DEFAULT 0",
            "action_history_json": "TEXT DEFAULT ''",
            "total_tokens": "INTEGER DEFAULT 0",
        })

        await _ensure_columns("test_step_logs", {
            "prompt_tokens": "INTEGER DEFAULT 0",
            "completion_tokens": "INTEGER DEFAULT 0",
            "total_tokens": "INTEGER DEFAULT 0",
            "perception_ms": "INTEGER DEFAULT 0",
            "llm_ms": "INTEGER DEFAULT 0",
            "action_ms": "INTEGER DEFAULT 0",
            "subgoal_index": "INTEGER",
            "subgoal_desc": "TEXT DEFAULT ''",
        })

        await _ensure_columns("test_cases", {
            "parameters": "TEXT DEFAULT ''",
        })
