-- Source-level section predictions: preserve provenance and numeric precision.
ALTER TABLE pred.run ADD COLUMN score_metadata jsonb;
ALTER TABLE pred.forecast ALTER COLUMN probability TYPE double precision;
ALTER TABLE pred.forecast_current ALTER COLUMN probability TYPE double precision;
ALTER TABLE pred.forecast_current ALTER COLUMN logged_probability TYPE double precision;
