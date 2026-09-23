# Collector score and warning issuance — draft

The product consumes `score.v3` with `feature_schema: feat.v3` and
`object_level: collector`. Every `collectors[].collector_id` is the level-2
`smvu.object_tree.object_id`; a tag prefix is not an object identifier. Collector
IDs must be unique. Legacy prefix scores are rejected by the product worker.

This draft does not close MOS-217 (sensor/section-level predictions) or MOS-219
(training and validation of a 24-hour model). The product worker accepts only a
24-hour collector score under the current team decision. The old 720-hour
candidate is historical research input, not an approved deployment.

`horizon_h` is a positive integer from the trained model metadata. The worker
checks the same horizon and `model_version` in the score, `GET /model` and
`POST /predict`. An explicit different worker horizon fails the run; the default
administrator setting cannot relabel a 720-hour prediction as 24 hours.

`collectors[]` contains `collector_id`, `p`, and the full `features` vector in
`feature_names` order. Current `warning_open` is informational. Orders consume
**all** entries in top-level `warnings`, including entries closed before the next
refresh. Each entry contains:

| Field | Meaning |
|---|---|
| `warning_id` | Stable exporter identity |
| `collector_id` | Object-tree collector key |
| `opened_at` | Original issuance time |
| `expires_at` | Original issuance time plus the trained horizon |
| `probability` | Original probability at issuance |
| `features` | Immutable feature vector at issuance |
| `closed_at`, `status` | Policy outcome at this snapshot; not a new issuance |

Naive archive timestamps are Europe/Moscow, consistent with SMVU ingestion.
The product deduplicates by model version, collector and normalized original
issuance time. It locks the corresponding `pred.warning` row and stores the
warning, section selection, notifications and work orders transactionally.
A failed dictionary lookup rolls back the issuance claim and every associated
order. `pred.warning_section` preserves the selected sections and original
explanation; `maint.notification.warning_section_id` is unique.

Order generation does not depend on the sparse `pred.forecast` journal or its
probability deadband. Two different warnings on one calendar date remain two
warnings. A retry, including a concurrent worker, cannot issue the same warning
again. Probability and explanation come from original warning vectors; `/predict`
checks them with the same model. Contributions describe `mean_tree_logit`, not an
additive decomposition of the final ensemble probability.

The configured `order_top_sections_per_object` limit is applied once per warning;
this PR does not change the current setting. Inspection sections are ranked by
D5 failure-episode weight and then `section_id`. The collector probability is
not a calibrated section probability. Ambiguous sections belonging to more than
one collector are excluded rather than assigned by channel majority. Warning
precision/recall is measured per collector warning; turning it into several
inspection orders does not create an order-level quality metric.

`due_at` is the original opening time plus the smaller of
`ref.priority.response_hours` and `ref.app_setting.order_preventive_cap_h`.
This preserves the master response norms of 16, 48 and 72 hours and its configured
cap. Delayed processing does not move the immutable-warning deadline. This
opening-time policy and separate orders per warning require review under
MOS-179/MOS-182; historical replay does not establish preventive usefulness. The end of a risk window is **not a predicted failure time**.

Orders API fields `forecast.risk_window_ends_at` and
`window_remaining_after_due_h` replace the misleading `predicted_failure_at` and
`lead_hours`. Warning orders have `source_system: forecast_warning`, a stable
`forecast.warning_id`, original `forecast.as_of`, probability and features, and
`forecast.forecast_id: null`. Their immutable prediction snapshot is rendered in
the order card. Legacy orders retain their forecast links.
