import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from Backend.db import connect, init_db
from Backend.electrical import load_reference, load_meter_energy, circuits_report, measured_parents
from Backend.insights import detect_anomalies
from Backend.dashboard import dashboard
from Backend.smart import smart, apply_action


class ElectricalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.path=self.root/'test.db'
        with connect(self.path) as c:
            init_db(c)
            c.executemany('INSERT INTO rooms(code,name,floor,zone,source) VALUES (?,?,?,?,?)',
                [('A','A','Etage 1','ESEO','occupancy'),('B','B','Etage 1','ESEO','occupancy')])
        self.patches=[patch('Backend.insights.connect',lambda:connect(self.path)),
                      patch('Backend.dashboard.connect',lambda:connect(self.path)),
                      patch('Backend.smart.connect',lambda:connect(self.path)),
                      patch('Backend.smart.LLM_PROVIDER','local')]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def circuit(self,circuit,rooms,energy=1,included=1,usage='Lighting'):
        sensor=f'/Hub/{circuit}_Energie_Active'
        with connect(self.path) as c:
            c.execute('INSERT INTO electrical_circuits VALUES (?,?,?,?,?,?)',(circuit,circuit,usage,None,'TECH','{}'))
            c.execute('INSERT INTO electrical_meters VALUES (?,?,?,?,?)',(sensor,circuit,circuit,usage,included))
            c.executemany('INSERT INTO circuit_rooms VALUES (?,?,?)',[(circuit,r,'["MSI"]') for r in rooms])
            c.executemany('INSERT INTO electrical_hourly VALUES (?,?,?,1,0)',
                         [(sensor,f'2026-01-12 {h:02d}:00:00',energy) for h in range(4)])
            c.execute("INSERT OR REPLACE INTO electrical_import VALUES ('complete','1')")
            c.execute("INSERT OR REPLACE INTO electrical_import VALUES ('reference','{}')")
        return sensor

    def occupancy(self,room,hours):
        with connect(self.path) as c:
            c.executemany('INSERT OR REPLACE INTO occupancy_hourly VALUES (?,?,?,4)',
                         [(room,f'2026-01-12 {h:02d}:00:00',occupied) for h,occupied in hours])

    def test_shared_feed_requires_all_rooms_observed_empty_and_breaks_gaps(self):
        self.circuit('Depart_001',['A','B'])
        self.occupancy('A',[(h,0) for h in range(4)])
        self.assertEqual(detect_anomalies()['anomalies'],[])
        self.occupancy('B',[(0,0),(1,0),(2,1),(3,0)])
        rows=detect_anomalies()['anomalies']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['duration_hours'],2)
        self.assertEqual(rows[0]['energy_kwh'],2)
        self.assertEqual(rows[0]['type'],'empty_zone_lighting')
        self.assertIsNone(rows[0]['room'])
        self.assertEqual(len(rows[0]['served_rooms']),2)

    def test_shared_energy_never_duplicated_per_room_or_with_parent(self):
        self.circuit('Depart_001',['A','B'],2)
        self.circuit('TGBT_001',['A','B'],20,included=0)
        self.occupancy('A',[(h,0) for h in range(4)])
        self.occupancy('B',[(h,0) for h in range(4)])
        d=dashboard()
        self.assertEqual(d['summary']['energy_kwh'],8)
        self.assertTrue(all(r['energy_kwh'] is None for r in d['rooms']))
        self.assertEqual(d['electrical']['summary']['shared_kwh'],8)
        self.assertEqual(detect_anomalies()['summary']['total_wasted_kwh'],8)

    def test_dedicated_feed_and_simulation_targets_actual_sensor(self):
        sensor=self.circuit('Depart_001',['A'])
        self.occupancy('A',[(h,0) for h in range(4)])
        card=smart(detect_anomalies())['actions'][0]
        self.assertEqual(card['location'],'Depart_001')
        command=apply_action(card['id'])
        self.assertEqual(command['circuit_ids'],['Depart_001'])
        self.assertEqual(command['sensor_ids'],[sensor])
        d=dashboard()
        self.assertEqual(next(r for r in d['rooms'] if r['code']=='A')['energy_kwh'],4)
        self.assertIsNone(next(r for r in d['rooms'] if r['code']=='B')['energy_kwh'])

    def test_unmapped_and_excluded_counters_never_create_absence_alert(self):
        self.circuit('Depart_001',[],50)
        self.circuit('Depart_002',['A'],50,included=0)
        self.occupancy('A',[(h,0) for h in range(4)])
        self.assertEqual(detect_anomalies()['anomalies'],[])
        with connect(self.path) as c:
            r=circuits_report(c,'2026-01-12','2026-01-13')
        self.assertEqual(r['summary']['unassigned_kwh'],200)

    def test_hierarchy_transitive_and_cycles_terminate(self):
        self.assertEqual(measured_parents({'root','leaf'},[('root','middle'),('middle','leaf')]),{'root'})
        self.assertEqual(measured_parents({'root'},[('root','middle'),('middle','root')]),set())

    def test_api_reports_exact_sensor_and_rejects_invalid_period(self):
        from Backend.app import app
        from fastapi.testclient import TestClient
        self.circuit('Depart_001',['A'])
        with patch('Backend.app.DB_PATH',self.path),patch('Backend.db.connect',lambda:connect(self.path)):
            client=TestClient(app)
            result=client.get('/api/energy/circuits?from=2026-01-12&to=2026-01-12')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['summary']['energy_kwh'],4)
            self.assertEqual(result.json()['circuits'][0]['hosted_room'],'TECH')
            self.assertEqual(client.get('/api/energy/circuits?from=invalid').status_code,422)

    @staticmethod
    def reference():
        structure=[]
        for number,room in enumerate(['A','B'],5):
            r=[None]*25;r[7]=room;r[6]='N1';r[5]='Etage 1';r[15]='ESEO';r[14]='Salle '+room
            r[19]='Depart_001' if room=='A' else ''
            if room=='A':r[21]='Extractor'
            structure.append((number,r))
        feed=[None]*19;feed[1]='Depart_001';feed[3]='Lumières';feed[8]="od:circuitUsage='Eclairage'";feed[6]='TECH'
        extractor=[None]*19;extractor[1]='Extractor';extractor[2]='BIM_EXACT';extractor[6]='ROOF';extractor[11]='Depart_001'
        # Explicit service A must win over the roof location.
        matches={'bim_exact':[{'id':'CDE1','name':'Extracteur','technical_path':'CVC','localisation':'Campus/N1/A','nomenclature':'BIM_EXACT','source_row':12}]}
        return structure,{'Depart_001':(5,feed),'Extractor':(6,extractor)},{},matches,set()

    def test_reference_joins_exact_nomenclature_and_service_not_hosting(self):
        with connect(self.path) as c,patch('Backend.electrical.read_reference',return_value=self.reference()):
            report=load_reference(c,self.root)
            rooms=[r['room'] for r in c.execute('SELECT room FROM circuit_rooms')]
            asset=json.loads(c.execute('SELECT payload FROM circuit_assets').fetchone()[0])
        self.assertEqual(rooms,['A'])
        self.assertEqual(asset['hosted_room'],'ROOF')
        self.assertEqual(asset['served_rooms'],['A'])
        self.assertEqual(asset['cde_match_status'],'exact')
        self.assertTrue(asset['localisation_conflict'])
        self.assertEqual(report['cde_matched_assets'],1)

    def test_counter_deltas_cross_month_reset_and_thermal_excluded(self):
        folder=self.root/'energy_csv';folder.mkdir()
        sensor='/Hub/Depart_001_Energie_Active'
        fields=['timestamp','display name','sensor id','value','resource','re2020Usage']
        for name,rows in [('energy_01.csv',[('2026-01-31 23:00:00',100),('2026-01-31 23:30:00',101)]),
                          ('energy_02.csv',[('2026-02-01 00:00:00',103),('2026-02-01 00:30:00',1),('2026-02-01 00:45:00',2)])]:
            with (folder/name).open('w',newline='',encoding='utf-8') as f:
                w=csv.writer(f,delimiter=';');w.writerow(fields)
                for t,v in rows:w.writerow([t,'Light',sensor,v,'Electricity','Lighting'])
                w.writerow([rows[0][0],'Heat','/Hub/CircuitEC_001_Heat_energy',9999,'Water','Heating'])
        with connect(self.path) as c,patch('Backend.electrical.read_reference',return_value=self.reference()):
            load_meter_energy(c,self.root)
            rows=[dict(r) for r in c.execute('SELECT * FROM electrical_hourly ORDER BY hour')]
            self.assertEqual(c.execute('SELECT COUNT(*) FROM electrical_meters').fetchone()[0],1)
        self.assertEqual([r['energy_kwh'] for r in rows],[1,3])
        self.assertEqual(rows[1]['resets'],1)


if __name__=='__main__':unittest.main()
