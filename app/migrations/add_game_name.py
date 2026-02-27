"""
Migration: Add game_name column to streams table.
Run once after deployment: python -m app.migrations.add_game_name
"""
from app.database.database import engine
from sqlalchemy import text


def migrate():
    with engine.connect() as conn:
        # Check if column already exists
        try:
            conn.execute(text("SELECT game_name FROM streams LIMIT 1"))
            print("✅ Column 'game_name' already exists, skipping.")
        except Exception:
            conn.execute(text("ALTER TABLE streams ADD COLUMN game_name VARCHAR"))
            conn.commit()
            print("✅ Added 'game_name' column to streams table.")


if __name__ == "__main__":
    migrate()