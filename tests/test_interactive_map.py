from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from fastapi.testclient import TestClient
from Backend.app import app


class InteractiveMapTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)

    def test_map_and_navigation(self):
        self.assertEqual(self.client.get('/map').status_code,200)
        self.assertIn('href="/map"',self.client.get('/').text)
        self.assertIn('id="back"',self.client.get('/map').text)

    def test_all_floor_exports_have_selectable_room_geometry(self):
        for floor in ['N0','N1','N2','N3','N4','N5','S1']:
            with self.subTest(floor=floor):
                response=self.client.get(f'/exports/{floor}.svg')
                self.assertEqual(response.status_code,200)
                svg=ET.fromstring(response.content)
                rooms=[e for e in svg.iter() if e.get('data-selectable')=='true']
                self.assertTrue(rooms)
                self.assertTrue(all(e.get('data-room-code') or e.get('data-code') for e in rooms))

    def test_missing_floor_returns_404(self):
        self.assertEqual(self.client.get('/exports/unknown.svg').status_code,404)
