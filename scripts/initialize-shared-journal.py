"""Provision a new Bot's Journal from the account creation transaction."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from shared_context import activate_empty

root=Path(sys.argv[1])
root.mkdir(parents=True,exist_ok=True)
with closing(sqlite3.connect(root/'behavior.sqlite')) as db:
    if not activate_empty(db,{},group_limit=int(sys.argv[2])):
        raise ValueError('bot_journal_initialization_conflicts_with_existing_history')
