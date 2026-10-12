"""User journal entries and reversible removal of simulated commands."""
import json
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .db import connect


class JournalEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=140)
    target: str = Field(default='', max_length=100)
    note: str = Field(default='', max_length=1000)

    @field_validator('title','target','note')
    @classmethod
    def clean(cls,value,info):
        value=value.strip()
        if info.field_name=='title' and not value:
            raise ValueError('Le titre est obligatoire.')
        return value


def ensure_tables(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS journal_entries(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS journal_removed(id TEXT PRIMARY KEY,removed_at TEXT NOT NULL);
    ''')


def entries():
    with connect() as conn:
        ensure_tables(conn)
        items=[dict(json.loads(r['payload']),id=r['id'],kind='manual') for r in conn.execute('SELECT * FROM journal_entries')]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='commands'").fetchone():
            items.extend(dict(json.loads(r['payload']),id='simulation:'+r['action_id'],kind='simulation') for r in conn.execute('SELECT * FROM commands'))
        removed={r[0] for r in conn.execute('SELECT id FROM journal_removed')}
        return sorted((i for i in items if i['id'] not in removed),key=lambda i:i['created_at'],reverse=True)


def add(entry):
    item=dict(entry.model_dump(),id='manual:'+uuid4().hex,kind='manual',created_at=datetime.now(timezone.utc).isoformat())
    with connect() as conn:
        ensure_tables(conn)
        conn.execute('INSERT INTO journal_entries VALUES (?,?)',(item['id'],json.dumps(item,ensure_ascii=False)))
    return item


def remove(entry_id,restore=False):
    with connect() as conn:
        ensure_tables(conn)
        exists=conn.execute('SELECT 1 FROM journal_entries WHERE id=?',(entry_id,)).fetchone()
        if not exists and entry_id.startswith('simulation:') and conn.execute("SELECT 1 FROM sqlite_master WHERE name='commands'").fetchone():
            exists=conn.execute('SELECT 1 FROM commands WHERE action_id=?',(entry_id.removeprefix('simulation:'),)).fetchone()
        if not exists:raise KeyError(entry_id)
        if restore:conn.execute('DELETE FROM journal_removed WHERE id=?',(entry_id,))
        else:conn.execute('INSERT OR REPLACE INTO journal_removed VALUES (?,?)',(entry_id,datetime.now(timezone.utc).isoformat()))
    return {'id':entry_id,'restored':restore}
