"""Development launcher — sets a local DB path so the app runs outside Docker."""
import os
import sys

# Ensure the taskreminder package is importable when run from any directory
sys.path.insert(0, os.path.dirname(__file__))

# Store the dev database alongside this file
os.environ.setdefault(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(__file__), "dev.db"),
)

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
