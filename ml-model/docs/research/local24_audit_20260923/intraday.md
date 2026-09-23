# Frozen intraday numeric hypothesis — 23 September 2026

Hypothesis frozen before the full raw scan or model training: within-channel
numeric dynamics from the preceding complete day can add information beyond
canonical V3 episode history. Root research runner evaluates the one addition
against its unchanged control and validation protocol. No 2026 selection.

For each of `gas`, `temperature`, `ups`, exactly six features:

| Suffix after `intra_<kind>_` | Meaning |
|---|---|
| `range_z_max` | Maximum channel daily range / same-channel prior 28-day SD |
| `std_z_max` | Maximum channel daily sample SD / same-channel prior SD |
| `iqr_z_max` | Maximum channel daily interquartile range / prior SD |
| `plateau_share_mean` | Mean channel proportion of adjacent equal numeric records |
| `changed_constant_share` | Fraction of observed channels differing from a constant prior baseline |
| `numeric_n_log1p` | `log(1 + retained numeric records)` as coverage information |

Ratios are clipped to 10. Scales pool all retained numeric observations within
each individual channel in `[feature_day−28 days, feature_day)`. The sample SD
uses within-day and between-day sums of squared deviations around the past-only
pooled mean. There is no globally fitted scaler or cross-instrument physical
average. No prior variance or insufficient prior observations produces zero
ratios; the constant-change flag distinguishes change from a constant history.
No observations yields zero, with the count feature identifying missingness.

Source files are `data/02_interim/mk/parquet/j2022.parquet` through `j2026.parquet`.
Only records from 1 April 2022 onward are used; 2021 and earlier are excluded.
All timestamps satisfy `ts < as_of`; only the complete preceding calendar day
is summarized. Source timestamps retain the archive's Moscow-naive convention.
Frozen Local24 mapping and all 4,267,349 eligible section/day keys are reused.
Labels are not read or modified when constructing features.

Per the customer QA instruction, values −3276, −127, −100 and 255 are removed
only when the alarm flag is false or absent. Alarm-coded records are retained
in **all** numeric statistics, including scale, extrema and IQR; these features
describe registered journal signals, not verified physical measurements.
Unknown codes are not guessed. Metadata separately counts removals and retained
alarm-coded records. Exact raw identity includes `(ch, ts, ev, val, alarm)`.
Plateau ordering is `ts, ev, val, alarm`; conflicting timestamp/event keys are
reported. The tie-break is deterministic but does not establish a physical
ordering of simultaneous conflicting records. Static instrument mapping and
unverified historical type changes remain limitations.

New module: `src/ml/local24_intraday_features.py`. New output folder:
`data/03_processed/local24_intraday_20260923`, with `intraday_features.parquet`,
`numeric_daily.parquet`, yearly small summaries, and `metadata.json` including
source/output SHA-256 values. No raw archive is copied or overwritten.

Verification before the full build: three tests passed. Independent raw-prefix
rebuilds at 15 May 2025 and 15 May 2026 agree exactly on feature vectors for six
actual high-volume channels, covering all three types. Actual variable-day
extrema, sample variance and IQR match a separate direct raw query. An explicit
service-code test checks alarm identity, exact deduplication and a constant
baseline. These checks prove supplied-archive prefix consistency, not historical
online delivery timing.

Full build completed: 4,267,349 unique frozen keys, 18 finite float32 features,
zero null or nonfinite feature rows. The filtered types supplied 156,912,376
numeric raw rows. Of these, 4,803 non-alarm known-code records were excluded;
475,630 exact duplicate extras were removed, leaving 156,431,943 records in
527,954 channel/day summaries. No alarm-coded known codes or conflicting
timestamp/event keys were present in this selected source. Numeric observations
are available on 289,894 eligible gas section/days, 184,696 temperature
section/days, and 1,291 UPS section/days; these counts overlap. Nonzero plateau
features occur on just 8,191, 1,100 and 14 rows respectively. This is a sparse
journal signal, not evidence of uniform physical sampling or a complete time
integral. The model experiment must determine usefulness.

Output SHA-256:
`4fd8206aae02777d63c3c5de1e8fae335ba130f5f982627aa6dd3f3cbc694a1c`.
Final targeted tests: **3 passed in 6.41 seconds**.

Run from this worktree:

```sh
LOCAL24_ROOT=/Users/Nik/Projects/lct-2026-task8 PYTHONPATH=src /usr/local/anaconda3/bin/python3 -m pytest tests/ml/test_local24_intraday_features.py -q
PYTHONPATH=src /usr/local/anaconda3/bin/python3 -m ml.local24_intraday_features --root /Users/Nik/Projects/lct-2026-task8 --output /Users/Nik/Projects/lct-2026-task8/data/03_processed/local24_intraday_20260923
```
