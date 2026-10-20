"""Apply database migrations: `DATABASE_URL=... python scripts/migrate.py`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bluff.db import apply_migrations, resolve_database_url  # noqa: E402

if __name__ == "__main__":
    applied = apply_migrations(resolve_database_url(sys.argv[1] if len(sys.argv) > 1 else None))
    print("Applied: " + ", ".join(applied) if applied else "Database already up to date.")
