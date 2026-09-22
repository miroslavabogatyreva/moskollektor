SET default_transaction_read_only = on;
-- Прогоны и строки pred.forecast по версиям модели: сколько выдала модель v3
-- и на какие срезы. Только чтение.
SELECT r.model_version, r.status, count(DISTINCT r.run_id) AS runs,
       min(r.run_id), max(r.run_id), min(r.as_of), max(r.as_of)
  FROM pred.run r GROUP BY 1, 2 ORDER BY 1, 2;
SELECT r.as_of, r.status, count(DISTINCT r.run_id) AS runs, min(r.run_id), max(r.run_id),
       count(f.*) AS rows, count(*) FILTER (WHERE f.probability >= 0.63) AS ge063,
       min(f.horizon_h) AS h_min, max(f.horizon_h) AS h_max
  FROM pred.run r LEFT JOIN pred.forecast f USING (run_id)
 WHERE r.model_version = 'lgbm-v3-bag-2026.09.21' GROUP BY 1, 2 ORDER BY 1, 2;
SELECT key, value FROM ref.app_setting
 WHERE key IN ('forecast_horizon_h', 'order_threshold_a', 'order_threshold_b',
               'order_threshold_c', 'precision_min', 'recall_min') ORDER BY key;
