"""Scoped SQLite BM25 candidates, using the existing database, not a second store.

Design comparison: LivingMemory's scoped BM25 + Chinese bigrams. This small
implementation uses stdlib SQLite, the platform's tokenizer and existing facts.
The index is disposable; primary records and their versions remain authoritative.
"""
import json
from hosted_memory import tokens, ALIASES
from memory_scope import fact_rows,fact_item,summary_rows,summary_item


class MemorySearch:
    def __init__(self, db):
        self.db = db
        db.executescript('''
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
          kind UNINDEXED,scope UNINDEXED,subject UNINDEXED,key UNINDEXED,
          revision UNINDEXED,terms);
        CREATE TRIGGER IF NOT EXISTS search_fact_changed AFTER UPDATE ON facts BEGIN
          DELETE FROM memory_search WHERE kind='fact' AND scope=OLD.scope AND subject=OLD.subject AND key=OLD.name;
        END;
        CREATE TRIGGER IF NOT EXISTS search_fact_deleted AFTER DELETE ON facts BEGIN
          DELETE FROM memory_search WHERE kind='fact' AND scope=OLD.scope AND subject=OLD.subject AND key=OLD.name;
        END;
        CREATE TRIGGER IF NOT EXISTS search_summary_deleted AFTER DELETE ON memory_summaries BEGIN
          DELETE FROM memory_search WHERE kind='summary' AND key=CAST(OLD.id AS TEXT);
        END;
        CREATE TRIGGER IF NOT EXISTS search_summary_changed AFTER UPDATE ON memory_summaries BEGIN
          DELETE FROM memory_search WHERE kind='summary' AND key=CAST(OLD.id AS TEXT);
        END;''')
        db.commit()

    def search(self, scope, subject, query, summaries=False, limit=12):
        kind = 'summary' if summaries else 'fact'
        person = '' if summaries else subject
        if summaries:
            rows=summary_rows(self.db,scope,subject)
            docs=[(str(r[0]),str(r[3]),r[1],r[4]) for r in rows]
        else:
            rows=fact_rows(self.db,scope,subject)
            docs=[(r[0],str(r[3]),r[0]+' '+r[1],r[10]) for r in rows]
        if not docs:return []
        old={(r[0],r[1]):str(r[2]) for r in self.db.execute('SELECT scope,key,revision FROM memory_search WHERE kind=? AND subject=? LIMIT 20000',(kind,person))}
        with self.db:
            for key,revision,text,source_scope in docs:
                if old.get((source_scope,key))==revision:continue
                self.db.execute('DELETE FROM memory_search WHERE kind=? AND scope=? AND subject=? AND key=?',(kind,source_scope,person,key))
                self.db.execute('INSERT INTO memory_search VALUES(?,?,?,?,?,?)',(kind,source_scope,person,key,revision,' '.join(sorted(tokens(text)))))
        terms = tokens(query)
        for name, aliases in ALIASES.items():
            if any(a in query for a in aliases):terms.update(tokens(name))
        if not terms:return []
        match = ' OR '.join('"'+t.replace('"','""')+'"' for t in sorted(terms)[:64])
        # Join the authoritative bounded candidate set before ranking. Stale
        # indexes / another person's summaries cannot enter via a broad MATCH.
        allowed=json.dumps([[d[3],d[0],d[1]] for d in docs])
        hits=self.db.execute('''SELECT m.scope,m.key FROM memory_search m JOIN json_each(?) a
          ON m.scope=json_extract(a.value,'$[0]') AND m.key=json_extract(a.value,'$[1]') AND m.revision=json_extract(a.value,'$[2]')
          WHERE memory_search MATCH ? AND m.kind=? AND m.subject=? ORDER BY bm25(memory_search) LIMIT ?''',(allowed,match,kind,person,limit)).fetchall()
        by_key={(r[4] if summaries else r[10],str(r[0])):r for r in rows}
        selected=[by_key[tuple(r)] for r in hits if tuple(r) in by_key]
        if summaries:
            return [summary_item(r,'scoped_bm25') for r in selected]
        return [fact_item(r,subject,'scoped_bm25') for r in selected]
