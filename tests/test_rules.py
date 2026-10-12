import csv
import json
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from fastapi.testclient import TestClient
from Backend.db import connect,init_db
from Backend.app import app
from Backend.insights import detect_anomalies
from Backend.rules import settings,save_settings,business_alerts,Decision,qualify,decision_history,allowed,Schedule,Interval,recommendation_input
from Backend.etl import load_occupancy
from Backend.smart import smart


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'test.db'
        with connect(self.path) as conn:
            init_db(conn)
            conn.executemany('INSERT INTO rooms(code,name,floor,zone,source) VALUES (?,?,?,?,?)',
                [('A','Salle A','Etage 1','ESEO','occupancy+ifc'),('B','Salle B','Etage 1','ESEO','occupancy+ifc')])
        self.patches=[patch(name,lambda:connect(self.path)) for name in ['Backend.insights.connect','Backend.dashboard.connect','Backend.rules.connect','Backend.smart.connect','Backend.db.connect']]
        self.patches.extend([patch('Backend.app.DB_PATH',self.path),patch('Backend.smart.LLM_PROVIDER','local')])
        for item in self.patches:item.start()
        self.client=TestClient(app)

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def observations(self, rows):
        with connect(self.path) as conn:
            conn.executemany('INSERT OR REPLACE INTO occupancy_hourly VALUES (?,?,?,?)',rows)

    def config(self, **values):
        value=settings().model_copy(update=values)
        # Revalidate mutations so tests exercise the same schema as the API.
        save_settings(type(value).model_validate(value.model_dump()))

    def report(self,begin='2026-01-12',end='2026-01-12'):
        return business_alerts(detect_anomalies(begin,end))

    def by_rule(self,rule,**kwargs):
        return [a for a in self.report(**kwargs)['alerts'] if a['rule_id']==rule]

    def test_normal_and_missing_data_are_not_presence(self):
        self.observations([('A','2026-01-12 01:00:00',0,2)])
        self.assertEqual(self.report()['alerts'],[])
        self.assertEqual(self.report('2026-01-13','2026-01-13')['alerts'],[])

    def test_night_hours_config_boundary_and_exception(self):
        self.observations([('A',f'2026-01-12 {h:02d}:00:00',1,1) for h in [1,3,4,6]])
        self.config(night_end_hour=4,exceptions=[Interval(room='A',start='2026-01-12T01:00:00',end='2026-01-12T02:00:00',reason='Maintenance')])
        rows=detect_anomalies('2026-01-12','2026-01-12',types=['night_presence'])['anomalies']
        self.assertEqual(len(rows),1);self.assertIn('03:00:00',rows[0]['start_at'])
        self.assertEqual(rows[0]['duration_hours'],1)
        self.assertIn('00h–04h',rows[0]['evidence'])

    def test_night_across_midnight_and_disable(self):
        self.observations([('A',f'2026-01-12 {h:02d}:00:00',1,1) for h in [1,6,22]])
        self.config(night_start_hour=22,night_end_hour=6)
        self.assertEqual(len(self.by_rule('REG-01')),2)
        enabled=settings().enabled;enabled['REG-01']=False;self.config(enabled=enabled)
        self.assertEqual(self.by_rule('REG-01'),[])

    def test_room_calendar_overrides_zone_and_missing_calendar(self):
        self.observations([('A','2026-01-12 07:00:00',1,1),('B','2026-01-12 07:00:00',1,1)])
        self.assertEqual(self.by_rule('REG-02'),[])
        self.config(schedules=[Schedule(scope='zone',key='ESEO',days=[0,1,2,3,4],start_hour=8,end_hour=18),Schedule(key='A',days=[0],start_hour=6,end_hour=18)])
        alerts=self.by_rule('REG-02');self.assertEqual([a['room'] for a in alerts],['B'])
        self.assertIn('08h–18h',alerts[0]['reason'])

    def test_opening_shift_after_midnight_uses_previous_weekday(self):
        calendar=Schedule(key='A',days=[0],start_hour=22,end_hour=6)
        self.assertTrue(allowed(calendar,datetime(2026,1,13,1)))
        self.assertFalse(allowed(calendar,datetime(2026,1,12,1)))
        self.assertFalse(allowed(calendar,datetime(2026,1,13,6)))

    def test_closure_overlap_exception_and_safe_recommendation(self):
        self.observations([('A','2026-01-12 10:00:00',1,1),('A','2026-01-12 11:00:00',1,1)])
        self.config(closures=[Interval(room='A',start='2026-01-12T10:00:00',end='2026-01-12T12:00:00',reason='Travaux')],exceptions=[Interval(room='A',start='2026-01-12T11:00:00',end='2026-01-12T12:00:00',reason='Intervention autorisée')])
        report=self.report();alerts=[a for a in report['alerts'] if a['rule_id']=='REG-03']
        self.assertEqual(len(alerts),1);self.assertEqual(alerts[0]['severity'],'high')
        self.assertEqual(alerts[0]['end_at'],'2026-01-12 11:00:00')
        action=smart(recommendation_input(report['raw'],report))['actions'][0]
        self.assertEqual(action['command'],'review_schedule');self.assertEqual(action['estimated_saving_eur'],0)
        self.assertEqual(action['evidence'][0]['rule_id'],'REG-03')

    def test_invalid_and_future_data_are_quality_signals(self):
        self.observations([('A','bad-time',1,1),('A','2999-01-01 00:00:00',1,1),('UNKNOWN','2026-01-12 01:00:00',1,1)])
        alerts=self.by_rule('REG-04');self.assertEqual(len(alerts),2)
        self.assertTrue(all(not a['can_locate'] for a in alerts))
        self.assertEqual(self.by_rule('REG-01'),[])
        self.assertIsNone(self.report()['freshness']['last_measurement'])

    def test_freshness_requires_declared_cadence_and_stays_historical(self):
        self.observations([('A','2026-01-12 00:00:00',0,1),('B','2026-01-12 23:00:00',0,1)])
        self.assertEqual(self.by_rule('REG-05'),[])
        self.config(expected_hourly_presence=True,freshness_hours=3)
        alert=self.by_rule('REG-05')[0];self.assertEqual(alert['room'],'A')
        self.assertIn('23 h',alert['reason']);self.assertTrue(self.report()['freshness']['historical'])

    def test_traceability_unknown_conflicting_and_bim_missing(self):
        with connect(self.path) as conn:
            conn.execute("INSERT INTO electrical_import VALUES ('complete','1')")
            conn.execute("INSERT INTO electrical_import VALUES ('reference','{}')")
            conn.execute("INSERT INTO electrical_circuits VALUES ('Depart_X','Unknown','Lighting',NULL,NULL,'{}')")
            conn.execute("INSERT INTO circuit_assets VALUES ('Depart_X','device',?)",(json.dumps({'id':'device','name':'Capteur','cde_match_status':'ambiguous','localisation_conflict':True}),))
            conn.execute("UPDATE rooms SET source='occupancy' WHERE code='B'")
        alerts=self.by_rule('REG-07');self.assertEqual(len(alerts),1)
        self.assertIn('renseignée',alerts[0]['reason']);self.assertFalse(alerts[0]['can_locate'])
        alerts=self.by_rule('REG-09');self.assertEqual(len(alerts),2)
        self.assertTrue(all(not a['can_locate'] for a in alerts))

    def test_recurrence_groups_distinct_episodes_and_decisions_persist(self):
        self.observations([('A',f'2026-01-{d:02d} 01:00:00',1,1) for d in [12,13,14]])
        self.config(repeat_threshold=3)
        report=self.report('2026-01-12','2026-01-14');groups=[a for a in report['alerts'] if a['rule_id']=='REG-08']
        self.assertEqual(len(groups),1);self.assertEqual(groups[0]['occurrences'],3)
        self.assertFalse(any(a['rule_id']=='REG-01' for a in report['alerts']))
        aid=groups[0]['id'];qualify(aid,Decision(status='resolved',note='Vérifié'))
        self.assertEqual(self.report('2026-01-12','2026-01-14')['alerts'][0]['status'],'resolved')
        self.assertEqual(len(decision_history()),1)
        self.assertIn('Signal récurrent',json.loads(decision_history()[0]['context'])['title'])
        self.observations([('A','2026-01-15 01:00:00',1,1)])
        evolved=next(a for a in self.report('2026-01-12','2026-01-15')['alerts'] if a['id']==aid)
        self.assertEqual(evolved['status'],'new');self.assertIn('evolution',evolved)

    def test_single_day_view_detects_previous_recurrences(self):
        self.observations([('A',f'2026-01-{d:02d} 01:00:00',1,1) for d in [10,11,12]])
        alerts=self.by_rule('REG-08')
        self.assertEqual(len(alerts),1);self.assertEqual(alerts[0]['occurrences'],3)
        child=alerts[0]['children'][0]
        self.assertEqual(self.client.get('/api/alerts/'+child['id']).status_code,200)

    def test_qualification_api_and_resolved_alert_not_sent_to_ai(self):
        self.observations([('A','2026-01-12 01:00:00',1,1)])
        report=self.client.get('/api/alerts?from=2026-01-12&to=2026-01-12').json();aid=report['alerts'][0]['id']
        self.assertEqual(self.client.post(f'/api/alerts/{aid}/decision',json={'status':'resolved'}).status_code,200)
        self.assertEqual(self.client.post(f'/api/alerts/{aid}/decision',json={'status':'invented'}).status_code,422)
        self.assertEqual(self.client.post('/api/alerts/unknown/decision',json={'status':'resolved'}).status_code,404)
        result=self.client.get('/api/insights/smart?from=2026-01-12&to=2026-01-12&preview=true').json()
        self.assertEqual(result['actions'],[])

    def test_month_limit_on_every_period_endpoint_and_default(self):
        self.observations([('A','2026-01-12 01:00:00',0,1)])
        for path in ['/api/insights/raw','/api/insights/smart','/api/dashboard','/api/map/data','/api/energy/circuits','/api/alerts']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path+'?from=2026-01-01&to=2026-02-01').status_code,422)
                self.assertEqual(self.client.get(path+'?from=2026-01-01&to=2026-01-31'+('&preview=true' if path.endswith('smart') else '')).status_code,200)
        self.assertLessEqual(datetime.fromisoformat(detect_anomalies()['period']['to'])-datetime.fromisoformat(detect_anomalies()['period']['from']),timedelta(days=31))

    def test_config_validation_and_change_history(self):
        value=settings().model_dump(mode='json');value['night_end_hour']=0
        self.assertEqual(self.client.put('/api/rules',json=value).status_code,422)
        value['night_end_hour']=4;value['schedules']=[{'scope':'room','key':'UNKNOWN','days':[0],'start_hour':8,'end_hour':18}]
        self.assertEqual(self.client.put('/api/rules',json=value).status_code,422)
        value['schedules']=[];self.assertEqual(self.client.put('/api/rules',json=value).status_code,200)
        with connect(self.path) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM rule_settings_history').fetchone()[0],1)

    def test_historical_comparison_requires_complete_comparable_data(self):
        enabled=settings().enabled;enabled['REG-10']=True;self.config(enabled=enabled)
        self.observations([('A','2026-02-02 08:00:00',1,1)])
        self.assertEqual(self.by_rule('REG-10',begin='2026-02-02',end='2026-02-02'),[])
        rows=[]
        hour=datetime(2026,1,5)
        while hour<datetime(2026,2,3):
            rows.append(('A',str(hour),int(hour.date()==datetime(2026,2,2).date()),1));hour+=timedelta(hours=1)
        self.observations(rows)
        self.assertEqual(len(self.by_rule('REG-10',begin='2026-02-02',end='2026-02-02')),1)

    def test_import_deduplicates_quarantines_conflicts_and_is_idempotent(self):
        path=self.root/'presence.csv'
        with path.open('w',newline='',encoding='utf-8') as handle:
            writer=csv.writer(handle,delimiter=';');writer.writerow(['timestamp','sensor id','room','value'])
            writer.writerows([['2026-01-12 01:00:00','s','A','True'],['2026-01-12 01:00:00','s','A','True'],
                ['bad','s','A','True'],['2026-01-12 02:00:00','s','UNKNOWN','True'],['2026-01-12 03:00:00','s','A','junk'],
                ['2026-01-12 04:00:00','s','A','True'],['2026-01-12 04:00:00','s','A','False']])
        with patch('Backend.etl.occupancy_files',return_value=[path]):
            snapshots=[]
            for iteration in range(2):
                with connect(self.path) as conn:
                    load_occupancy(conn)
                    snapshots.append([tuple(r) for r in conn.execute('SELECT * FROM occupancy_hourly')])
                    audit=json.loads(conn.execute('SELECT payload FROM presence_import_audit').fetchone()[0])
                self.assertEqual(audit['duplicates'],2);self.assertEqual(audit['conflicting_values'],1)
                self.assertEqual(audit['invalid_timestamp'],1);self.assertEqual(audit['unknown_room'],1)
            self.assertEqual(snapshots[0],snapshots[1]);self.assertEqual(snapshots[0],[('A','2026-01-12 01:00:00',1,1)])
            self.assertEqual(len(self.by_rule('REG-01')),1)


if __name__=='__main__':unittest.main()
