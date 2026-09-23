"""Warm-up history changes reporting flags, never event identity or targets."""
from datetime import datetime

import duckdb

from ml.local24_data import Local24Data


def test_repeat_flags_use_qualified_warmup_and_strictly_earlier_onset():
    with duckdb.connect() as c:
        c.execute("SET threads=3; SET memory_limit='6GB'")
        c.execute('''CREATE TABLE ep(ch BIGINT,section_id BIGINT,collector_id BIGINT,
            t_start TIMESTAMP,t_end TIMESTAMP)''')
        c.executemany('INSERT INTO ep VALUES (?,?,1,?,?)', [
            (1,10,'2022-01-02 00:00:00','2022-01-02 02:00:00'),
            (1,10,'2022-04-02 00:00:00','2022-04-02 02:00:00'),
            (2,10,'2022-04-02 00:00:00','2022-04-02 02:00:00'),
            (3,20,'2022-01-02 00:00:00','2022-01-02 01:00:00'),
            (3,20,'2022-04-02 00:00:00','2022-04-02 02:00:00'),
            (4,20,'2022-04-02 00:00:00','2022-04-02 02:00:00'),
            (3,20,'2022-04-03 00:00:00',None),
        ])
        builder=Local24Data.__new__(Local24Data)
        builder.con=c
        builder.cutoff=datetime(2022,4,4)
        builder._prepare_events()
        assert c.execute('SELECT channel_id,t_start::DATE,is_repeat,channel_is_repeat FROM events ORDER BY t_start,channel_id').fetchall()==[
            (1,datetime(2022,4,2).date(),True,True),
            (2,datetime(2022,4,2).date(),True,False),
            (3,datetime(2022,4,2).date(),False,False),
            (4,datetime(2022,4,2).date(),False,False),
            (3,datetime(2022,4,3).date(),True,True),
        ]
        # Exact 1h warm-up episode does not qualify; simultaneous firsts remain first.
        assert c.execute("SELECT count(*) FROM events WHERE t_start<'2022-04-01'").fetchone()==(0,)
        expected=c.execute('''SELECT ch,section_id,collector_id,t_start,t_end,
            t_start+INTERVAL 3601 SECOND,
            concat(ch,':',strftime(t_start,'%Y-%m-%dT%H:%M:%S'))
            FROM ep WHERE t_start>='2022-04-01'
              AND coalesce(t_end,TIMESTAMP '2022-04-04')>t_start+INTERVAL 1 HOUR
            ORDER BY t_start,ch''').fetchall()
        actual=c.execute('SELECT * EXCLUDE(is_repeat,channel_is_repeat) FROM events ORDER BY t_start,channel_id').fetchall()
        assert actual==expected
