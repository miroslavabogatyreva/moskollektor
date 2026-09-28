-- MOS-264: only existing daily aggregates; no refresh and no raw journal scan.
-- Run psql -X -q -v ON_ERROR_STOP=1 with PGOPTIONS enforcing read-only/timeout.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '2s';
SET LOCAL timezone = 'Europe/Moscow';
COPY (
    SELECT channel_id, day, readings_total, fault_total, undefined_total, last_read
    FROM feat.channel_daily
    WHERE day >= DATE '2025-06-25' AND day < DATE '2026-07-01'
    ORDER BY channel_id, day
) TO STDOUT WITH CSV HEADER;
ROLLBACK;
