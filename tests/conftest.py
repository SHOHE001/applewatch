import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def pytest_collection_modifyitems(config, items):
    os.environ.setdefault("WEBHOOK_TOKEN", "test-token")
    os.environ.setdefault("CLAUDE_WATCH_DEBUG", "0")
