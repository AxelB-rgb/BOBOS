import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from Backend.db import connect,init_db
from Backend.app import app


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'journal.db'
        with connect(self.path) as conn:
            init_db(conn)
            conn.execute('CREATE TABLE commands(action_id TEXT PRIMARY KEY,payload TEXT,created_at TEXT)')
            self.command={'action_id':'test','command':'review_schedule','target':'A','created_at':'2026-01-12T01:00:00','message':'Simulation'}
            conn.execute('INSERT INTO commands VALUES (?,?,?)',('test',json.dumps(self.command),self.command['created_at']))
        self.patches=[patch('Backend.journal.connect',lambda:connect(self.path)),patch('Backend.app.DB_PATH',self.path)]
        for p in self.patches:p.start()
        self.client=TestClient(app)

    def tearDown(self):
        for p in self.patches:p.stop()
        self.temp.cleanup()

    def test_add_remove_restore_manual_entry(self):
        response=self.client.post('/api/actions/history',json={'title':' Contrôler le capteur ','target':'A','note':'Planning à vérifier'})
        self.assertEqual(response.status_code,201)
        entry=response.json();self.assertEqual(entry['title'],'Contrôler le capteur')
        key=entry['id']
        self.assertEqual(len(self.client.get('/api/actions/history').json()['commands']),2)
        self.assertEqual(self.client.delete('/api/actions/history/'+key).status_code,200)
        self.assertEqual(len(self.client.get('/api/actions/history').json()['commands']),1)
        self.assertEqual(self.client.post('/api/actions/history/'+key+'/restore').status_code,200)
        self.assertEqual(len(self.client.get('/api/actions/history').json()['commands']),2)

    def test_simulation_removal_preserves_command_and_is_idempotent(self):
        for _ in range(2):self.assertEqual(self.client.delete('/api/actions/history/simulation:test').status_code,200)
        self.assertEqual(self.client.get('/api/actions/history').json()['commands'],[])
        with connect(self.path) as conn:self.assertEqual(json.loads(conn.execute('SELECT payload FROM commands').fetchone()[0]),self.command)
        self.client.post('/api/actions/history/simulation:test/restore')
        self.assertEqual(len(self.client.get('/api/actions/history').json()['commands']),1)

    def test_validation_and_unknown_ids(self):
        for title in ['', '   ', 'x'*141]:
            self.assertEqual(self.client.post('/api/actions/history',json={'title':title}).status_code,422)
        self.assertEqual(self.client.delete('/api/actions/history/missing').status_code,404)
        self.assertEqual(self.client.post('/api/actions/history/missing/restore').status_code,404)
