"""Small durable control store. File bytes are never stored here."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import S3Error


class Store:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS buckets(name TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS credentials(access TEXT PRIMARY KEY, bucket TEXT NOT NULL REFERENCES buckets(name) ON DELETE CASCADE, expires INTEGER NOT NULL, record TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS credentials_expiry ON credentials(expires);
            ''')
        os.chmod(path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def bucket(self, name):
        with self.connection() as db:
            row = db.execute('SELECT record FROM buckets WHERE name=?', (name,)).fetchone()
        if not row:
            raise S3Error('NoSuchBucket', 'Bucket does not exist', 404)
        return json.loads(row[0])

    def register(self, name, record):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO buckets VALUES (?,?)', (name, json.dumps(record)))
            saved = json.loads(db.execute('SELECT record FROM buckets WHERE name=?', (name,)).fetchone()[0])
            if any(saved[k] != record[k] for k in ('root', 'directory')):
                raise S3Error('Conflict', 'Bucket already maps to a different directory; delete it before remapping', 409)
        return saved

    def delete_bucket(self, name):
        with self.connection() as db:
            db.execute('DELETE FROM buckets WHERE name=?', (name,))

    def grant(self, access, record):
        with self.connection() as db:
            db.execute('DELETE FROM credentials WHERE expires<=?', (int(time.time()),))
            db.execute('INSERT INTO credentials VALUES (?,?,?,?)', (access, record['bucket'], record['expires'], json.dumps(record)))

    def credential(self, access):
        with self.connection() as db:
            row = db.execute('SELECT record FROM credentials WHERE access=? AND expires>?', (access, int(time.time()))).fetchone()
        if not row:
            raise S3Error()
        return json.loads(row[0])

    def revoke(self, access):
        with self.connection() as db:
            db.execute('DELETE FROM credentials WHERE access=?', (access,))
