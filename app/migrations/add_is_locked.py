"""Migration: add is_locked column to streamers table."""
from sqlalchemy import text
from app.database.database import engine


def run():
    with engine.connect() as conn:
        # Check if column already exists (SQLite / PostgreSQL compatible)
        try:
            conn.execute(text("SELECT is_locked FROM streamers LIMIT 1"))
            print("Migration skipped: is_locked column already exists.")
            return
        except Exception:
            pass

        conn.execute(text("ALTER TABLE streamers ADD COLUMN is_locked BOOLEAN DEFAULT FALSE"))
        conn.commit()
        print("Migration applied: added is_locked column to streamers.")


if __name__ == "__main__":
    run()
