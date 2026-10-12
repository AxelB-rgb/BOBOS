"""Configurable business rules for a local historical-data demonstrator.

No calendar, exception, cadence or closure is inferred from a room name.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .db import connect

MAX_PERIOD_DAYS = 31
DEFAULTS_PATH = Path(__file__).with_name('rules.defaults.json')
RULE_NAMES = {
    'REG-01':'Présence à un horaire inhabituel',
    'REG-02':'Présence hors horaires renseignés',
    'REG-03':'Présence pendant une indisponibilité',
    'REG-04':'Qualité des données de présence',
    'REG-05':'Mesures de présence manquantes',
    'REG-06':'Départ électrique et salles sur la carte',
    'REG-07':'Traçabilité électrique à vérifier',
    'REG-08':'Signal récurrent',
    'REG-09':'Correspondance de référentiel manquante',
    'REG-10':'Variation de présence inhabituelle',
}


def validate_period(start, end):
    if end <= start:
        raise ValueError('La fin doit être postérieure au début.')
    if end-start > timedelta(days=MAX_PERIOD_DAYS):
        raise ValueError('Sélectionnez au maximum un mois (31 jours inclus).')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Schedule(StrictModel):
    scope: Literal['room','zone'] = 'room'
    key: str = Field(min_length=1,max_length=100)
    days: list[int] = Field(min_length=1,max_length=7)
    start_hour: int = Field(ge=0,le=23)
    end_hour: int = Field(ge=0,le=24)

    @model_validator(mode='after')
    def valid(self):
        if any(day not in range(7) for day in self.days) or len(set(self.days))!=len(self.days):
            raise ValueError('Jours : valeurs distinctes de 0 (lundi) à 6 (dimanche).')
        if self.start_hour==self.end_hour:
            raise ValueError('Les horaires de début et fin doivent différer.')
        return self


class Interval(StrictModel):
    room: str = Field(min_length=1,max_length=100)
    start: datetime
    end: datetime
    reason: str = Field(min_length=1,max_length=250)

    @model_validator(mode='after')
    def valid(self):
        if self.start.tzinfo or self.end.tzinfo:
            raise ValueError('Utilisez les heures locales du jeu de données, sans fuseau.')
        if self.end<=self.start:
            raise ValueError('La fin de la période doit suivre son début.')
        return self


class RuleSettings(StrictModel):
    night_start_hour: int = Field(default=0,ge=0,le=23)
    night_end_hour: int = Field(default=6,ge=0,le=23)
    future_tolerance_minutes: int = Field(default=5,ge=0,le=1440)
    expected_hourly_presence: bool = False
    freshness_hours: int = Field(default=3,ge=1,le=168)
    repeat_window_days: int = Field(default=7,ge=1,le=31)
    repeat_threshold: int = Field(default=3,ge=2,le=100)
    variation_ratio: float = Field(default=2,gt=1,le=20)
    variation_min_hours: int = Field(default=5,ge=1,le=100)
    reference_days: int = Field(default=28,ge=14,le=31)
    minimum_coverage: float = Field(default=.8,ge=.5,le=1)
    decision_retention_days: int = Field(default=180,ge=1,le=3650)
    enabled: dict[str,bool]
    priorities: dict[str,Literal['low','medium','high']]
    schedules: list[Schedule] = Field(default_factory=list,max_length=500)
    exceptions: list[Interval] = Field(default_factory=list,max_length=500)
    closures: list[Interval] = Field(default_factory=list,max_length=500)

    @model_validator(mode='after')
    def valid(self):
        if not self.enabled.get('REG-06',True):
            raise ValueError('REG-06 est une fonction permanente de la carte, pas un détecteur à désactiver.')
        if self.night_start_hour==self.night_end_hour:
            raise ValueError('La plage nocturne ne peut pas être vide.')
        if set(self.enabled)!=set(RULE_NAMES) or set(self.priorities)!=set(RULE_NAMES):
            raise ValueError('La configuration doit préciser les dix règles connues.')
        keys=[(s.scope,s.key) for s in self.schedules]
        if len(keys)!=len(set(keys)):
            raise ValueError('Un seul calendrier par salle ou zone ; celui de la salle est prioritaire.')
        return self


def ensure_tables(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS rule_settings(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS rule_settings_history(id INTEGER PRIMARY KEY,changed_at TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS alert_registry(id TEXT PRIMARY KEY,payload TEXT NOT NULL,last_seen TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS alert_decisions(id INTEGER PRIMARY KEY,alert_id TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL,context TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS idx_decision_alert ON alert_decisions(alert_id,id);
    CREATE TABLE IF NOT EXISTS presence_import_audit(source TEXT PRIMARY KEY,payload TEXT NOT NULL,imported_at TEXT NOT NULL);
    ''')
    if 'context' not in {r[1] for r in conn.execute('PRAGMA table_info(alert_decisions)')}:
        conn.execute("ALTER TABLE alert_decisions ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")


def settings(conn=None):
    if conn is None:
        with connect() as connection:
            return settings(connection)
    ensure_tables(conn)
    row=conn.execute('SELECT payload FROM rule_settings WHERE id=1').fetchone()
    return RuleSettings.model_validate_json(row[0] if row else DEFAULTS_PATH.read_text(encoding='utf-8'))


def save_settings(value: RuleSettings):
    with connect() as conn:
        ensure_tables(conn)
        rooms={r['code'] for r in conn.execute('SELECT code FROM rooms')}
        zones={r['zone'] for r in conn.execute('SELECT DISTINCT zone FROM rooms') if r['zone']}
        for item in [*value.exceptions,*value.closures]:
            if item.room!='*' and item.room not in rooms:
                raise ValueError(f'Salle inconnue : {item.room}.')
        for item in value.schedules:
            if item.key not in (rooms if item.scope=='room' else zones):
                raise ValueError(f'Salle ou zone inconnue : {item.key}.')
        payload=value.model_dump_json()
        conn.execute('INSERT OR REPLACE INTO rule_settings VALUES (1,?)',(payload,))
        conn.execute('INSERT INTO rule_settings_history(changed_at,payload) VALUES (?,?)',(datetime.now().isoformat(),payload))
    return {'settings':value.model_dump(mode='json'),'notice':'Paramètres de démonstration enregistrés. Validation métier nécessaire avant production.'}


def overlaps(interval, room, hour):
    return interval.room in ('*',room) and interval.start < hour+timedelta(hours=1) and interval.end > hour


def excepted(config, room, hour):
    return any(overlaps(item,room,hour) for item in config.exceptions)


def allowed(schedule, hour):
    if schedule.start_hour < schedule.end_hour:
        return hour.weekday() in schedule.days and schedule.start_hour<=hour.hour<schedule.end_hour
    # The after-midnight part belongs to the previous day's opening shift.
    return (hour.weekday() in schedule.days and hour.hour>=schedule.start_hour) or ((hour.weekday()-1)%7 in schedule.days and hour.hour<schedule.end_hour)


def make_alert(rule, key, title, reason, config, room=None, circuit=None, start=None, end=None, limitations='', occurrences=1, evidence=None, can_locate=True):
    return {'id':hashlib.sha256(f'{rule}:{key}'.encode()).hexdigest()[:24],
        'rule_id':rule,'rule_name':RULE_NAMES.get(rule,rule),'title':title,'reason':reason,
        'severity':config.priorities.get(rule,'medium'),'room':room,'circuit_id':circuit,
        'start_at':start,'end_at':end,'limitations':limitations,'occurrences':occurrences,
        'evidence':evidence or [],'can_locate':can_locate and bool(room or circuit),
        'status':'new','action':{'REG-01':'Confirmer une activité autorisée ou contrôler le capteur.', 'REG-02':'Vérifier l’activité et corriger le calendrier si nécessaire.', 'REG-03':'Contacter le responsable de la salle et vérifier une intervention autorisée.', 'REG-04':'Contrôler la source et corriger les données à l’import.', 'REG-05':'Vérifier le capteur et la transmission des mesures.', 'REG-07':'Vérifier les liens de salles dans le MSI et les correspondances CDE.', 'REG-08':'Examiner les épisodes et adapter le planning ou contrôler le capteur.', 'REG-09':'Corriger la correspondance avec le référentiel.', 'REG-10':'Comparer la hausse avec les activités et horaires connus.'}.get(rule,'Vérifier la présence et les contraintes techniques.')}


def episodes(rows):
    groups=defaultdict(list)
    for rule,room,hour,reason in rows:
        groups[(rule,room,reason)].append(hour)
    for (rule,room,reason),hours in groups.items():
        times=sorted(set(hours));begin=previous=times[0];count=1
        for hour in times[1:]:
            if hour-previous!=timedelta(hours=1):
                yield rule,room,begin,previous+timedelta(hours=1),reason,count
                begin=hour;count=0
            previous=hour;count+=1
        yield rule,room,begin,previous+timedelta(hours=1),reason,count


def catalogue(config):
    missing={'REG-02':None if config.schedules else 'Aucun calendrier de salle ou zone renseigné.',
        'REG-03':None if config.closures else 'Aucune indisponibilité renseignée.',
        'REG-05':None if config.expected_hourly_presence else 'Cadence attendue non confirmée : fraîcheur informative seulement.',
        'REG-10':'Comparaison désactivée tant que les seuils ne sont pas validés.' if not config.enabled['REG-10'] else None}
    return [{'id':key,'name':name,'enabled':config.enabled[key],
        'priority':config.priorities[key],'state':'disabled' if not config.enabled[key] else 'waiting' if missing.get(key) else 'active',
        'notice':missing.get(key) or ('Déjà disponible sur la carte.' if key=='REG-06' else 'Paramètres provisoires, à valider.')}
        for key,name in RULE_NAMES.items()]


def business_alerts(raw):
    """Reuse REG-01 and energy anomalies; do not create a second detector for them."""
    from .electrical import has_electrical
    start=datetime.fromisoformat(raw['period']['from']);end=datetime.fromisoformat(raw['period']['to'])
    with connect() as conn:
        config=settings(conn)
        rooms={r['code']:dict(r) for r in conn.execute('SELECT * FROM rooms')}
        output=[]
        for item in raw['anomalies']:
            rule='REG-01' if item['type']=='night_presence' else 'ENERGY'
            title='Présence à un horaire inhabituel à vérifier' if rule=='REG-01' else 'Consommation active sans présence'
            alert=make_alert(rule,item['id'],title,item['evidence'],config,room=item['room'],circuit=item.get('circuit_id'),start=item['start_at'],end=item['end_at'],evidence=[{k:v for k,v in item.items() if k not in ('equipment','served_rooms','meters')}])
            alert['id']=item['id']  # Reuse the existing event identity and map navigation.
            if rule=='ENERGY':alert['severity']=item['severity']
            alert['limitations']='Mesures horaires historiques ; présence binaire. Une activité autorisée ou un capteur bloqué reste possible.'
            output.append(alert)
        sampled=raw['summary']['truncated']
        calendar_start=min(start,end-timedelta(days=config.repeat_window_days)) if config.enabled['REG-08'] else start
        selected=[dict(r) for r in conn.execute("SELECT room,hour FROM occupancy_hourly WHERE hour>=? AND hour<? AND occupied=1 AND n_samples>0 AND datetime(hour) IS NOT NULL ORDER BY room,hour",(calendar_start.isoformat(sep=' '),end.isoformat(sep=' ')))]
        schedule_map={(s.scope,s.key):s for s in config.schedules}
        triggered=[]
        for row in selected:
            room=row['room'];hour=datetime.fromisoformat(row['hour']);meta=rooms.get(room,{})
            if room not in rooms or excepted(config,room,hour):continue
            schedule=schedule_map.get(('room',room)) or schedule_map.get(('zone',meta.get('zone')))
            if config.enabled['REG-02'] and schedule and not allowed(schedule,hour):
                triggered.append(('REG-02',room,hour,f'Présence hors du calendrier renseigné ({schedule.start_hour:02d}h–{schedule.end_hour:02d}h, jours {", ".join(["lun","mar","mer","jeu","ven","sam","dim"][d] for d in schedule.days)}).'))
            if config.enabled['REG-03']:
                for closure in config.closures:
                    if overlaps(closure,room,hour):
                        triggered.append(('REG-03',room,hour,f'Indisponibilité déclarée : {closure.reason}, du {closure.start:%d/%m/%Y %H:%M} au {closure.end:%d/%m/%Y %H:%M}.'))
        calendar_candidates=[]
        for rule,room,begin,finish,reason,count in episodes(triggered):
            alert=make_alert(rule,f'{room}:{begin}:{reason}',RULE_NAMES[rule],reason,config,room=room,start=str(begin),end=str(finish),limitations='Calendrier saisi par le gestionnaire, à maintenir à jour. Observations agrégées à l’heure.',evidence=[{'room':room,'start_at':str(begin),'end_at':str(finish)}])
            calendar_candidates.append(alert)
            if finish>start:output.append(alert)
        future_cutoff=(datetime.now()+timedelta(minutes=config.future_tolerance_minutes)).isoformat(sep=' ')
        freshness=conn.execute('SELECT MAX(hour) FROM occupancy_hourly WHERE room IN (SELECT code FROM rooms) AND datetime(hour) IS NOT NULL AND n_samples>0 AND occupied IN (0,1) AND hour<=?',(future_cutoff,)).fetchone()[0]
        imported=conn.execute('SELECT MAX(imported_at) FROM presence_import_audit').fetchone()[0]
        if config.enabled['REG-04']:
            cutoff=(datetime.now()+timedelta(minutes=config.future_tolerance_minutes)).isoformat(sep=' ')
            bad=list(conn.execute('''SELECT o.room,COUNT(*) n FROM occupancy_hourly o LEFT JOIN rooms r ON r.code=o.room
                WHERE datetime(o.hour) IS NULL OR o.occupied NOT IN (0,1) OR o.n_samples<=0 OR r.code IS NULL OR o.hour>? GROUP BY o.room''',(cutoff,)))
            for row in bad:
                output.append(make_alert('REG-04',row['room'],'Données de présence incohérentes',f"{row['n']} ligne(s) incohérente(s) dans occupancy_hourly : horodatage, valeur, échantillons ou salle à contrôler.",config,room=row['room'],can_locate=False,limitations='Les défauts sont techniques, ils ne prouvent aucune anomalie d’usage.'))
            for row in conn.execute('SELECT * FROM presence_import_audit'):
                audit=json.loads(row['payload']);issues=sum(audit.get(k,0) for k in ('invalid_timestamp','invalid_value','unknown_room','future_timestamp','duplicates','conflicting_values'))
                if issues:
                    output.append(make_alert('REG-04','import:'+row['source'],'Import : données écartées ou dédupliquées',f"{row['source']} : {issues} défaut(s) identifiés à l’import. {audit.get('duplicates',0)} doublon(s) neutralisé(s).",config,can_locate=False,limitations='Les totaux proviennent de l’audit du dernier import ; les données rejetées ne déclenchent pas d’alerte d’usage.'))
        if config.enabled['REG-05'] and config.expected_hourly_presence:
            horizon=min(end,datetime.fromisoformat(freshness)+timedelta(hours=1)) if freshness else end
            last=conn.execute('SELECT room,MAX(hour) hour FROM occupancy_hourly WHERE hour<? AND hour<=? AND n_samples>0 AND occupied IN (0,1) AND datetime(hour) IS NOT NULL AND room IN (SELECT code FROM rooms) GROUP BY room',(str(horizon),future_cutoff))
            for row in last:
                measured=datetime.fromisoformat(row['hour'])
                gap=(horizon-measured-timedelta(hours=1)).total_seconds()/3600
                if gap>=config.freshness_hours:
                    output.append(make_alert('REG-05',f"{row['room']}:{row['hour']}",'Mesures de présence manquantes',f"Dernière mesure le {measured:%d/%m/%Y à %H:%M} ; {gap:.0f} h sans mesure à la fin de la période disponible.",config,room=row['room'],start=str(measured),end=str(horizon),limitations='Cadence horaire déclarée dans les paramètres. Ne signifie pas que la salle est vide.'))
        electrical=has_electrical(conn)
        if electrical and (config.enabled['REG-07'] or config.enabled['REG-09']):
            links=defaultdict(list)
            for row in conn.execute('SELECT * FROM circuit_rooms'):links[row['circuit_id']].append(row['room'])
            assets=defaultdict(list)
            for row in conn.execute('SELECT * FROM circuit_assets'):assets[row['circuit_id']].append(json.loads(row['payload']))
            for circuit in conn.execute('SELECT id,label FROM electrical_circuits'):
                cid=circuit['id'];served=links[cid];missing=[r for r in served if r not in rooms]
                conflicts=[a for a in assets[cid] if a.get('localisation_conflict') or a.get('cde_match_status')=='ambiguous']
                if config.enabled['REG-07'] and (not served or missing or conflicts):
                    reason='Aucune salle renseignée ; la desserte réelle reste inconnue.' if not served else f'{len(missing)} salle(s) inconnue(s), {len(conflicts)} équipement(s) aux correspondances contradictoires ou ambiguës.'
                    output.append(make_alert('REG-07',cid,'Traçabilité électrique à vérifier',reason,config,circuit=cid,can_locate=bool(served) and not missing,limitations='Référentiels MSI 2022 et CDE 2024 ; contrôle du câblage nécessaire.'))
                unmatched=[a for a in assets[cid] if a.get('cde_match_status') in ('unmatched','ambiguous')]
                if config.enabled['REG-09'] and unmatched:
                    output.append(make_alert('REG-09','equipment:'+cid,'Équipements sans correspondance unique',f"{len(unmatched)} équipement(s) de {cid} sans rapprochement CDE unique : {', '.join(a.get('name') or a['id'] for a in unmatched[:3])}.",config,circuit=cid,can_locate=False,limitations='Le lien MSI ne constitue pas une localisation BIM/CDE certaine.'))
        if config.enabled['REG-09']:
            for room,meta in rooms.items():
                if meta.get('source')=='occupancy':
                    output.append(make_alert('REG-09','room:'+room,'Salle IoT sans correspondance BIM',f'{room} est connue du flux de présence mais sans correspondance IFC importée.',config,room=room,can_locate=False,limitations='Localisation sur le plan non certifiée.'))
        if config.enabled['REG-08']:
            lower=end-timedelta(days=config.repeat_window_days)
            # Recurrence must remain useful when the manager opens a single day.
            recent=raw
            if start>lower:
                from .insights import detect_anomalies
                thresholds=raw['thresholds']
                recent=detect_anomalies(str(lower),str(end),min_hours=thresholds['min_duration_hours'],
                    min_hvac_kwh=thresholds['min_hvac_kwh_per_hour'],min_lighting_kwh=thresholds['min_lighting_kwh_per_hour'],
                    business_hours_only=thresholds['business_hours_only'],limit=1000)
            candidates=[]
            for item in recent['anomalies']:
                rule='REG-01' if item['type']=='night_presence' else 'ENERGY'
                child=make_alert(rule,item['id'],'Présence à un horaire inhabituel' if rule=='REG-01' else 'Consommation active sans présence',item['evidence'],config,
                    room=item['room'],circuit=item.get('circuit_id'),start=item['start_at'],end=item['end_at'],
                    evidence=[{k:v for k,v in item.items() if k not in ('equipment','served_rooms','meters')}])
                child['id']=item['id'];child['type']=item['type'];candidates.append(child)
            candidates.extend(calendar_candidates)
            groups=defaultdict(list)
            def group_key(alert):
                kind=alert['evidence'][0].get('type',alert['rule_id']) if alert['evidence'] else alert['rule_id']
                return kind,alert['room'] or alert['circuit_id']
            for alert in candidates:
                if alert['start_at'] and datetime.fromisoformat(alert['start_at'])>=lower:
                    groups[group_key(alert)].append(alert)
            for (kind,place),items in groups.items():
                unique={a['id']:a for a in items}
                if len(unique)<config.repeat_threshold:continue
                items=sorted(unique.values(),key=lambda a:a['start_at'])
                if not any(datetime.fromisoformat(a['end_at'])>start for a in items):continue
                first=items[0]
                grouped=make_alert('REG-08',f'{kind}:{place}','Signal récurrent à traiter',f'{len(items)} épisodes distincts de même catégorie sur {config.repeat_window_days} jours. Dernier épisode : {items[-1]["start_at"]}.',config,
                    room=first['room'],circuit=first['circuit_id'],start=first['start_at'],end=items[-1]['end_at'],occurrences=len(items),evidence=[a['evidence'][0] for a in items if a['evidence']],
                    limitations='Récurrences recherchées dans la fenêtre configurée, y compris avant la journée sélectionnée. Compte partiel si les données sont échantillonnées.')
                grouped['children']=items
                grouped['related_anomaly_ids']=[a['id'] for a in output if group_key(a)==(kind,place)]
                output=[a for a in output if group_key(a)!=(kind,place)];output.append(grouped)
            sampled=sampled or recent['summary']['truncated']
        comparison_notice='Comparaison historique désactivée.'
        if config.enabled['REG-10']:
            variation,comparison_notice=historical_variation(conn,start,end,config)
            output.extend(variation)
        cutoff=(datetime.now()-timedelta(days=config.decision_retention_days)).isoformat()
        decisions={r['alert_id']:dict(r) for r in conn.execute('SELECT * FROM alert_decisions WHERE created_at>=? ORDER BY id',(cutoff,))}
        now=datetime.now().isoformat()
        for alert in output:
            meta=rooms.get(alert['room'],{})
            alert['room_name']=meta.get('name');alert['floor']=meta.get('floor')
            if alert['id'] in decisions:
                decision=decisions[alert['id']]
                context=json.loads(decision.get('context') or '{}')
                newer=alert.get('end_at') and context.get('end_at') and alert['end_at']>context['end_at']
                changed=alert['rule_id'] not in ('REG-08',) and context.get('reason') and alert['reason']!=context['reason']
                if decision['status'] in ('resolved','dismissed') and (newer or changed):
                    alert['evolution']='Nouvelles observations depuis la dernière qualification.'
                else:alert.update(status=decision['status'],decision=decision)
            for child in alert.get('children',[]):
                child_meta=rooms.get(child['room'],{})
                child['room_name']=child_meta.get('name');child['floor']=child_meta.get('floor')
                conn.execute('INSERT INTO alert_registry VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen',(child['id'],json.dumps(child,ensure_ascii=False),now))
            conn.execute('INSERT INTO alert_registry VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen',(alert['id'],json.dumps(alert,ensure_ascii=False),now))
        output.sort(key=lambda a:(a['status'] in ('dismissed','resolved'),-{'high':2,'medium':1,'low':0}[a['severity']],a['status']!='new',-a['occurrences'],a['rule_id'],a['id']))
        return {'period':raw['period'],'alerts':output,'catalogue':catalogue(config),
            'summary':{'total':len(output),'active':sum(a['status'] not in ('dismissed','resolved') for a in output),'new':sum(a['status']=='new' for a in output),'repeated':sum(a['occurrences']>1 for a in output)},
            'freshness':{'last_measurement':freshness,'imported_at':imported,'historical':True,'notice':'Jeu de données historique ; aucune réception en temps réel. Date de réception originale non disponible.'},
            'comparison_notice':comparison_notice,'sampled_usage_alerts':sampled,'raw':raw}


def historical_variation(conn,start,end,config):
    ref_start=start-timedelta(days=config.reference_days)
    rows=list(conn.execute('SELECT room,hour,occupied FROM occupancy_hourly WHERE hour>=? AND hour<? AND n_samples>0 AND occupied IN (0,1) AND datetime(hour) IS NOT NULL AND room IN (SELECT code FROM rooms) ORDER BY hour',(str(ref_start),str(end))))
    reference=defaultdict(list);current=defaultdict(list)
    for row in rows:
        hour=datetime.fromisoformat(row['hour']);key=(row['room'],hour.weekday(),hour.hour)
        if excepted(config,row['room'],hour):continue
        (reference if hour<start else current)[key].append(row['occupied'])
    current_hours=int((end-start).total_seconds()/3600)
    output=[];complete=0
    for room in {r['room'] for r in rows}:
        observed=expected=0;baseline=present=0.
        for key,values in current.items():
            if key[0]!=room:continue
            observed+=len(values)
            ref=reference.get(key,[])
            if len(ref)<2:continue
            expected+=len(values);baseline+=sum(ref)/len(ref)*len(values);present+=sum(values)
        if observed<current_hours*config.minimum_coverage or expected<current_hours*config.minimum_coverage:continue
        # Check reference coverage as well, using the declared hourly cadence.
        reference_count=sum(len(values) for key,values in reference.items() if key[0]==room)
        if reference_count<config.reference_days*24*config.minimum_coverage:continue
        complete+=1
        if present-baseline>=config.variation_min_hours and present>=max(baseline,1)*config.variation_ratio:
            output.append(make_alert('REG-10',f'{room}:{start}:{end}','Hausse de présence à examiner',f'{present:.0f} h avec présence contre {baseline:.1f} h attendues selon les mêmes jours de semaine et heures des {config.reference_days} jours précédents.',config,room=room,start=str(start),end=str(end),limitations='Comparaison descriptive de présence binaire ; vérifier activités, calendrier et qualité du capteur. Aucune cause déduite.'))
    return output,f'{complete} salle(s) avec une couverture suffisante et des jours/heures comparables.' if complete else 'Historique insuffisant ou incomplet : aucune variation déduite.'


class Decision(StrictModel):
    status: Literal['confirmed','dismissed','watching','resolved']
    note: str = Field(default='',max_length=500)


def qualify(alert_id, decision: Decision):
    with connect() as conn:
        ensure_tables(conn)
        row=conn.execute('SELECT payload FROM alert_registry WHERE id=?',(alert_id,)).fetchone()
        if not row:
            raise KeyError(alert_id)
        conn.execute('INSERT INTO alert_decisions(alert_id,status,note,created_at,context) VALUES (?,?,?,?,?)',(alert_id,decision.status,decision.note,datetime.now().isoformat(),row[0]))
    return {'alert_id':alert_id,'status':decision.status,'notice':'Qualification enregistrée. Aucun équipement commandé.'}


def decision_history():
    with connect() as conn:
        config=settings(conn)
        cutoff=(datetime.now()-timedelta(days=config.decision_retention_days)).isoformat()
        return [dict(r) for r in conn.execute('SELECT d.*,a.payload FROM alert_decisions d LEFT JOIN alert_registry a ON a.id=d.alert_id WHERE d.created_at>=? ORDER BY d.id DESC LIMIT 500',(cutoff,))]


def recommendation_input(raw, report):
    """Only active usage signals go to the LLM; referential quality has no energy action."""
    excluded=set()
    additions=[]
    for alert in report['alerts']:
        if alert['rule_id']=='REG-08':
            excluded.update(alert.get('related_anomaly_ids',[]))
        resolved=alert['status'] in ('dismissed','resolved')
        if resolved:
            excluded.add(alert['id'])
            excluded.update(child['id'] for child in alert.get('children',[]))
            excluded.update(alert.get('related_anomaly_ids',[]))
            continue
        if alert['rule_id'] not in ('REG-02','REG-03','REG-08','REG-10'):continue
        additions.append({'id':alert['id'],'type':{'REG-02':'outside_schedule','REG-03':'room_unavailable','REG-08':'recurring_signal','REG-10':'presence_variation'}[alert['rule_id']],
            'rule_id':alert['rule_id'],'severity':alert['severity'],'room':alert['room'],'room_name':alert.get('room_name'),
            'floor':alert.get('floor'),'zone':None,'circuit_id':alert.get('circuit_id'),
            'start_at':alert['start_at'] or raw['period']['from'],'end_at':alert['end_at'] or raw['period']['to'],
            'duration_hours':max(1,int((datetime.fromisoformat(alert['end_at'])-datetime.fromisoformat(alert['start_at'])).total_seconds()/3600)) if alert['start_at'] and alert['end_at'] else 1,
            'energy_kwh':0,'occupied':None,'occupancy_samples':None,'meters':[],
            'evidence':alert['reason'],'suggested_instruction':alert['action']})
    kept=[dict(a,rule_id='REG-01' if a['type']=='night_presence' else 'ENERGY') for a in raw['anomalies'] if a['id'] not in excluded]
    result=dict(raw);result['anomalies']=additions+kept
    result['anomalies'].sort(key=lambda a:-{'high':2,'medium':1,'low':0}[a['severity']])
    result['summary']=dict(raw['summary'],anomaly_count=len(result['anomalies']))
    return result
