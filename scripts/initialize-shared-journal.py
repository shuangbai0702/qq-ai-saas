"""Initialize a newly created Bot's sole main Journal before runtime admission."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from shared_context import bootstrap

root=Path(sys.argv[1]).resolve()
root.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(root/'behavior.sqlite') as db:
    bootstrap(db)
