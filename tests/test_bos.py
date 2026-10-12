import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from Backend.db import connect, init_db
from Backend.insights import detect_anomalies
from Backend.smart import Proposal, validate_proposals, smart, apply_action, history
from Backend.app import app


class BOSIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'test.db'
        self.conn = connect(self.path)
        init_db(self.conn)
        self.conn.executemany('INSERT INTO rooms(code,name,floor,zone,source) VALUES (?,?,?,?,?)',
            [('A','Salle A','Etage 1','ESEO','occupancy+ifc'),('B','Salle B','Etage 1','ESEO','occupancy')])
        # Gap at 02:00 must break the island; occupied hour at 06:00 must end it.
        for h in [0,1,3,4,5,6]:
            time = f'2026-01-12 {h:02d}:00:00'
            self.conn.execute('INSERT INTO occupancy_hourly VALUES (?,?,?,?)',('A',time,int(h==6),4))
            self.conn.execute('INSERT INTO energy_hourly VALUES (?,?,?,?,?,?,?,?)',(time,'room','A','Etage 1','ESEO','Heating',1,'CVC A'))
            self.conn.execute('INSERT INTO energy_hourly VALUES (?,?,?,?,?,?,?,?)',(time,'zone','','Etage 1','ESEO','Heating',10,'Zone'))
        self.conn.commit()
        self.conn.close()
        self.patches = [patch('Backend.insights.connect',lambda:connect(self.path)),
                      patch('Backend.rules.connect',lambda:connect(self.path)),
                        patch('Backend.dashboard.connect',lambda:connect(self.path)),
                        patch('Backend.smart.connect',lambda:connect(self.path)),
                        patch('Backend.app.DB_PATH',self.path),patch('Backend.smart.OPENAI_API_KEY',''),patch('Backend.smart.LLM_PROVIDER','auto')]
        for p in self.patches:p.start()
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_islands_missing_occupancy_and_totals(self):
        raw=detect_anomalies(limit=1)
        self.assertEqual(raw['summary']['anomaly_count'],2)
        self.assertEqual(raw['summary']['returned_count'],1)
        self.assertEqual(raw['summary']['total_wasted_kwh'],5)
        self.assertEqual(raw['anomalies'][0]['duration_hours'],3)
        self.assertNotIn('empty_zone_hvac',raw['summary']['by_type'])
        all_rows=detect_anomalies()['anomalies']
        self.assertEqual(sorted(a['duration_hours'] for a in all_rows),[2,3])

    def test_night_presence_one_hour_and_safe_action(self):
        with connect(self.path) as conn:
            conn.execute("INSERT INTO occupancy_hourly VALUES ('B','2026-01-12 01:00:00',1,4)")
        raw=detect_anomalies(types=['night_presence'])
        self.assertEqual(raw['summary']['anomaly_count'],1)
        anomaly=raw['anomalies'][0]
        self.assertTrue(anomaly['occupied'])
        self.assertEqual(anomaly['duration_hours'],1)
        self.assertEqual(anomaly['energy_kwh'],0)
        self.assertEqual(smart(raw)['actions'][0]['command'],'review_schedule')
        self.assertEqual(smart(raw)['actions'][0]['estimated_saving_eur'],0)
        unsafe=Proposal(title='Couper',reason='Nuit',instruction='Couper',anomaly_ids=[anomaly['id']],command='hvac_eco')
        with self.assertRaises(ValueError):validate_proposals([unsafe],{anomaly['id']:anomaly})
        self.assertEqual(detect_anomalies(types=['night_presence'],business_hours_only=True)['anomalies'],[])

    def test_map_keeps_unmeasured_circuit_and_human_equipment_names(self):
        with connect(self.path) as conn:
            conn.execute("INSERT INTO electrical_circuits VALUES ('Depart_999','Éclairage salle A','Lighting',NULL,NULL,'{}')")
            conn.execute("INSERT INTO circuit_rooms VALUES ('Depart_999','A','[]')")
            conn.execute("INSERT INTO electrical_import VALUES ('complete','1')")
            conn.execute("INSERT INTO electrical_import VALUES ('reference','{}')")
            payload=json.dumps({'id':'VC_001','name':'VC_001','hosted_room':'A','cde_matches':[{'name':'Ventilo-convecteur'}]})
            conn.execute("INSERT INTO circuit_assets VALUES ('Depart_999','VC_001',?)",(payload,))
        with patch('Backend.db.connect',lambda:connect(self.path)):
            data=self.client.get('/api/map/data').json()
            circuit=next(c for c in data['electrical']['circuits'] if c['circuit_id']=='Depart_999')
            self.assertIsNone(circuit['energy_kwh'])
            self.assertTrue(circuit['unmeasured'])
            self.assertEqual(circuit['rooms'][0]['code'],'A')
            assets=self.client.get('/api/energy/circuits/Depart_999/assets').json()
            self.assertEqual(assets['assets'][0]['name'],'Ventilo-convecteur')
            self.assertEqual(self.client.get('/api/energy/circuits/invented/assets').status_code,404)

    def test_zone_requires_complete_coverage(self):
        with connect(self.path) as conn:
            conn.executemany('INSERT INTO occupancy_hourly VALUES (?,?,0,4)',
                [('B',f'2026-01-12 {h:02d}:00:00') for h in [0,1,3,4,5,6]])
        raw=detect_anomalies(types=['empty_zone_hvac'])
        self.assertEqual(raw['summary']['anomaly_count'],2)
        self.assertEqual(raw['summary']['total_wasted_kwh'],50)

    def test_api_validation(self):
        for query in ['from=not-a-date','from=2026-02-01&to=2026-01-01','types=invented','min_hvac_kwh=0']:
            self.assertEqual(self.client.get('/api/insights/raw?'+query).status_code,422)
        self.assertEqual(self.client.get('/api/dashboard?from=bad').status_code,422)
        self.assertEqual(self.client.post('/api/actions/unknown/apply').status_code,404)

    def test_smart_and_idempotent_command(self):
        response=self.client.get('/api/insights/smart')
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data['source'],'local')
        aid=data['actions'][0]['id']
        first=self.client.post(f'/api/actions/{aid}/apply').json()
        second=self.client.post(f'/api/actions/{aid}/apply').json()
        self.assertEqual(first,second)
        self.assertEqual(first['status'],'simulated')
        self.assertEqual(len(history()),1)
        self.assertTrue(self.client.get('/api/insights/smart').json()['actions'][0]['applied'])

    def test_no_anomalies_no_invented_actions(self):
        self.assertEqual(self.client.get('/api/insights/smart?from=2030-01-01&to=2030-01-01').json()['actions'],[])

    def test_llm_validation_and_fallback(self):
        raw=detect_anomalies()
        evidence={a['id']:a for a in raw['anomalies']}
        bad=Proposal(title='Bad',reason='Bad',instruction='Bad',anomaly_ids=['invented'],command='hvac_eco')
        with self.assertRaises(ValueError):validate_proposals([bad],evidence)
        wrong=Proposal(title='Bad',reason='Bad',instruction='Bad',anomaly_ids=[raw['anomalies'][0]['id']],command='lighting_off')
        with self.assertRaises(ValueError):validate_proposals([wrong],evidence)
        with patch('Backend.smart.OPENAI_API_KEY','test-key'),patch('Backend.smart.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value.json.return_value={'choices':[{'message':{'content':json.dumps({'actions':[bad.model_dump()]})}}]}
            result=smart(raw)
            self.assertEqual(result['source'],'local_fallback')
            self.assertGreater(len(result['actions']),0)

    def test_zone_lighting_scope(self):
        with connect(self.path) as conn:
            for h in [0,1]:
                time=f'2026-01-12 {h:02d}:00:00'
                conn.execute('INSERT INTO occupancy_hourly VALUES (?,?,0,4)',('B',time))
                conn.execute("INSERT INTO energy_hourly VALUES (?,'zone','','Etage 1','ESEO','Lighting',2,'Général éclairage')",(time,))
        rows=detect_anomalies(types=['empty_zone_lighting'])['anomalies']
        self.assertEqual(len(rows),1)
        self.assertIsNone(rows[0]['room'])
        self.assertEqual(rows[0]['energy_kwh'],4)
        card=smart(detect_anomalies(types=['empty_zone_lighting']))['actions'][0]
        self.assertEqual(card['command'],'lighting_off')
        self.assertEqual(card['estimated_saving_eur'],0.8)

    def test_general_meters_are_not_room_loads(self):
        from Backend.etl import scope_for
        self.assertEqual(scope_for('00-51-S','TD RDC - Départ Général ECLAIRAGE 1 - Energie active'),'zone')
        self.assertEqual(scope_for('04-41-N','TGBT - Départ Général Ventilo 4'),'zone')
        self.assertEqual(scope_for('04-41-N','Ventilo salle 04-41-N'),'room')

    def test_ifc_surface_units_and_capacity(self):
        from Backend.bim import geometry
        path=Path(self.tmp.name)/'surface.ifc'
        path.write_text("""#20=IFCSIUNIT(*,.LENGTHUNIT.,.CENTI.,.METRE.);
#1=IFCCARTESIANPOINT((0.,0.));
#2=IFCCARTESIANPOINT((400.,0.));
#3=IFCCARTESIANPOINT((400.,800.));
#4=IFCCARTESIANPOINT((0.,800.));
#5=IFCPOLYLINE((#1,#2,#3,#4,#1));
#6=IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#5);
#7=IFCEXTRUDEDAREASOLID(#6,#12,#13,300.);
#8=IFCSHAPEREPRESENTATION(#11,'Body','SweptSolid',(#7));
#9=IFCPRODUCTDEFINITIONSHAPE($,$,(#8));
#10=IFCSPACE('guid',#11,'01-01-S',$,$,#12,#9,'Salle',.ELEMENT.,.INTERNAL.,$);""",encoding='utf-8')
        self.assertEqual(geometry(path)['01-01-S'],(32.0,8,'estimated_from_ifc_area'))

    def test_ifc_names_with_apostrophes(self):
        from Backend.etl import parse_ifc_rooms
        path=Path(self.tmp.name)/'names.ifc'
        path.write_text("#1=IFCSPACE('guid',#2,'01-01-S',$,$,#3,#4,'Locaux d''entretien',.ELEMENT.,.INTERNAL.,$);",encoding='utf-8')
        self.assertEqual(parse_ifc_rooms(path),[('01-01-S',"Locaux d'entretien")])

    def test_ollama_structured_generation(self):
        raw=detect_anomalies()
        proposal=Proposal(title='Consulter les données',reason='Absence mesurée',instruction='Vérifier puis simuler',
                          anomaly_ids=['A1'],command='hvac_eco')
        with patch('Backend.smart.LLM_PROVIDER','ollama'),patch('Backend.smart.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value.json.return_value={'message':{'content':json.dumps({'action_1':proposal.model_dump()})}}
            data=smart(raw)
            self.assertEqual(data['source'],'ollama')
            self.assertIn('éco',data['actions'][0]['title'])
            self.assertIn('hors-gel',data['actions'][0]['instruction'])
            payload=client.return_value.__enter__.return_value.post.call_args.kwargs['json']
            self.assertFalse(payload['stream'])
            self.assertEqual(payload['format']['type'],'object')
            again=smart(raw)
            self.assertEqual(again['source'],'ollama')
            self.assertEqual(client.return_value.__enter__.return_value.post.call_count,1)
        with patch('Backend.smart.LLM_PROVIDER','ollama'),patch('Backend.smart.llm_status',return_value={'models':['llama3.2:latest']}):
            with self.assertRaises(ValueError):smart(raw,model_override='unknown')

    def test_llm_success(self):
        raw=detect_anomalies()
        p=Proposal(title='Passer en mode éco',reason='Présence nulle observée',instruction='Vérifier puis simuler',
                   anomaly_ids=[raw['anomalies'][0]['id']],command='hvac_eco')
        with patch('Backend.smart.OPENAI_API_KEY','test-key'),patch('Backend.smart.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value.json.return_value={'choices':[{'message':{'content':json.dumps({'actions':[p.model_dump()]})}}]}
            data=smart(raw)
            self.assertEqual(data['source'],'openai')
            self.assertIn('éco',data['actions'][0]['title'])
            self.assertIn('Salle A',data['actions'][0]['title'])
            payload=client.return_value.__enter__.return_value.post.call_args.kwargs['json']
            self.assertTrue(payload['response_format']['json_schema']['strict'])

    def test_dashboard_missing_is_unknown(self):
        rooms=self.client.get('/api/dashboard').json()['rooms']
        unobserved=next(r for r in rooms if r['code']=='B')
        self.assertIsNone(unobserved['utilization_pct'])
        self.assertIsNone(unobserved['capacity'])


if __name__=='__main__':unittest.main()
