-- Immutable warning issuance is independent of the sparse forecast journal.
CREATE TABLE pred.warning (
    warning_key text PRIMARY KEY,
    collector_id bigint NOT NULL REFERENCES smvu.object_tree(object_id),
    opened_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > opened_at),
    horizon_h integer NOT NULL CHECK (horizon_h > 0),
    probability double precision NOT NULL CHECK (probability BETWEEN 0 AND 1),
    model_version text NOT NULL,
    features jsonb NOT NULL,
    first_run_id bigint NOT NULL REFERENCES pred.run(run_id),
    orders_created_at timestamptz,
    UNIQUE (collector_id, opened_at, model_version)
);
CREATE TABLE pred.warning_section (
    id bigserial PRIMARY KEY,
    warning_key text NOT NULL REFERENCES pred.warning(warning_key),
    section_id bigint NOT NULL REFERENCES ref.object_xref(section_id),
    explanation_ru text NOT NULL,
    UNIQUE (warning_key, section_id)
);
ALTER TABLE maint.notification ADD COLUMN warning_section_id bigint REFERENCES pred.warning_section(id);
CREATE UNIQUE INDEX uq_notification_warning_section ON maint.notification(warning_section_id)
    WHERE warning_section_id IS NOT NULL;
ALTER TABLE maint.notification ADD CONSTRAINT notification_warning_required
    CHECK (source_system <> 'forecast_warning' OR warning_section_id IS NOT NULL);
