#!/usr/bin/env python3
"""Replay a real collector score into an isolated migrated DB with real registries.

Usage: DATABASE_URL=... PYTHONPATH=backend python code/check_warning_replay.py score.json
This intentionally writes to the supplied acceptance database; never production.
"""
import asyncio
import copy
import json
import os
from datetime import timedelta
from pathlib import Path
import sys

import asyncpg
from app.domain import order_rules
from app.worker import run_v3, score_v3
from app.api import orders
from app.db import _init_conn


async def check(path):
    data = score_v3.прочитать(path)
    assert data['object_level'] == 'collector' and data['warnings'], 'real warning history required'
    dsn = os.environ['DATABASE_URL']
    conn = await asyncpg.connect(dsn)
    try:
        run_id = await conn.fetchval("INSERT INTO pred.run(status,as_of,model_version) VALUES('done',$1,$2) RETURNING run_id",
                                     score_v3.момент(data['as_of']), data['model_version'])
        score = await run_v3.собрать(conn, str(path), run_id, data['horizon_h'], 'sensor_failure', 12)
        # Fresh connections concurrently replay the entire history after durable commit.
        async def replay():
            other = await asyncpg.connect(dsn)
            try:
                return await order_rules.завести_предупреждения(other, run_id, score, data['horizon_h'])
            finally:
                await other.close()
        before = await conn.fetchval('SELECT count(*) FROM maint.notification')
        initial = await asyncio.gather(replay(), replay())
        first = {'заявок':sum(r['заявок'] for r in initial)}
        count = await conn.fetchval('SELECT count(*) FROM maint.notification')
        assert count - before == first['заявок']
        assert await conn.fetchval('SELECT count(*) FROM pred.warning WHERE model_version=$1', data['model_version']) == len(data['warnings'])
        repeats = await asyncio.gather(replay(), replay())
        assert all(r['заявок'] == 0 for r in repeats)
        assert await conn.fetchval('SELECT count(*) FROM maint.notification') == count
        cap = float(await conn.fetchval("SELECT value FROM ref.app_setting WHERE key='order_preventive_cap_h'"))
        rows = await conn.fetch("""SELECT n.id,n.reported_at,n.due_at,n.long_text,w.opened_at,w.expires_at,
                w.probability,w.features,p.response_hours,w.horizon_h
            FROM maint.notification n JOIN pred.warning_section ws ON ws.id=n.warning_section_id
            JOIN pred.warning w ON w.warning_key=ws.warning_key
            JOIN ref.priority p ON p.id=n.priority_id WHERE w.model_version=$1""", data['model_version'])
        assert rows
        for row in rows:
            assert row['reported_at'] == row['opened_at']
            assert row['due_at'] == row['opened_at'] + timedelta(hours=min(row['response_hours'],cap))
            detail = await orders.get_order(row['id'], conn, None)
            assert detail['forecast']['probability'] == row['probability']
            assert detail['forecast']['risk_window_ends_at'] == row['expires_at']
            assert 'predicted_failure_at' not in detail['forecast']
            assert detail['forecast']['opening_features'] == json.loads(row['features'])
        # DB regressions use original real object/features and an explicitly named variant.
        variant = copy.deepcopy(score)
        variant['модель'] += '-transaction-regression'
        w = copy.deepcopy(data['warnings'][0])
        t = score_v3.момент(w['opened_at']).replace(hour=1,minute=0,second=0,microsecond=0)
        variant['warnings'] = []
        for offset in (0, 1):
            fresh = dict(w, opened_at=(t+timedelta(hours=offset)).isoformat(),
                         expires_at=(t+timedelta(hours=offset+data['horizon_h'])).isoformat())
            variant['warnings'].append(fresh)
        # Exercise the actual API JSONB codec as well as the raw worker connection.
        await _init_conn(conn)
        tx = conn.transaction()
        await tx.start()
        try:
            # No forecast journal rows at all are needed for these two distinct same-day warnings.
            journal = await conn.fetchval('SELECT count(*) FROM pred.forecast WHERE run_id=$1',run_id)
            assert journal == 0
            result = await order_rules.завести_предупреждения(conn,run_id,variant,data['horizon_h'])
            assert result['заявок'] >= 2
            encoded = await conn.fetchval('SELECT features FROM pred.warning WHERE model_version=$1 LIMIT 1', variant['модель'])
            assert isinstance(encoded, list) and encoded == w['features']
            again = await order_rules.завести_предупреждения(conn,run_id,variant,data['horizon_h'])
            assert again['заявок'] == 0
            assert await conn.fetchval('SELECT count(*) FROM pred.warning WHERE model_version=$1', variant['модель']) == 2
            # Missing activity dictionary must not leave the warning claimed or a half-order.
            broken = copy.deepcopy(variant)
            broken['модель'] += '-missing-dictionary'
            await conn.execute("UPDATE ref.order_type SET code='BROK' WHERE code='PREV'")
            count_before = await conn.fetchval('SELECT count(*) FROM maint.notification')
            try:
                await order_rules.завести_предупреждения(conn,run_id,broken,data['horizon_h'])
            except (ValueError,LookupError) as e:
                assert 'work order' in str(e) or 'order_type' in str(e)
            else:
                raise AssertionError('missing dictionary accepted')
            assert await conn.fetchval('SELECT count(*) FROM maint.notification') == count_before
            assert await conn.fetchval('SELECT count(*) FROM pred.warning WHERE model_version=$1', broken['модель']) == 0
            # A skipped work-order insert must roll back a warning already claimed and
            # its notification. The trigger exists only in this rolled-back transaction.
            await conn.execute("UPDATE ref.order_type SET code='PREV' WHERE code='BROK'")
            await conn.execute("""CREATE FUNCTION pg_temp.skip_warning_work_order() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$;
                CREATE TRIGGER test_skip_warning_work_order BEFORE INSERT ON maint.work_order
                FOR EACH ROW EXECUTE FUNCTION pg_temp.skip_warning_work_order()""")
            broken['модель'] += '-insert-failure'
            try:
                await order_rules.завести_предупреждения(conn,run_id,broken,data['horizon_h'])
            except ValueError as e:
                assert 'work order was not inserted' in str(e)
            else:
                raise AssertionError('skipped work-order insertion accepted')
            assert await conn.fetchval('SELECT count(*) FROM maint.notification') == count_before
            assert await conn.fetchval('SELECT count(*) FROM pred.warning WHERE model_version=$1', broken['модель']) == 0
        finally:
            await tx.rollback()
        result = {'real_warnings':len(data['warnings']), 'real_orders':len(rows),
                  'initial_created':first['заявок'], 'concurrent_first_created':[r['заявок'] for r in initial], 'concurrent_replay_created':[r['заявок'] for r in repeats],
                  'same_day_distinct_warning_orders':True,'journal_independent':True,
                  'original_onset_probability_features':True,'missing_dictionary_rollback':True,
                  'work_order_insert_failure_rollback':True,'api_jsonb_codec':True,
                  'scope':'historical 720h lower-level lifecycle; product worker rejects this model under MOS-219',
                  'response_cap_h':cap,
                  'top_sections_test_setting':int(await conn.fetchval(
                      "SELECT value FROM ref.app_setting WHERE key='order_top_sections_per_object'"))}
        print(json.dumps(result,indent=2))
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(check(Path(sys.argv[1])))
