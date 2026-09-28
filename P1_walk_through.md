# HomeIQ P1 Walk-Through: Taxonomy Classification and Cost Model

*2026-08-28T02:14:43Z by Showboat 0.6.1*
<!-- showboat-id: 545bd111-3c06-4f68-ae23-673cb8ffa59a -->

This document explains, runs, and validates HomeIQ phase P1: project taxonomy classification and the per-class quantile cost model built on P0's matched permits.

The specification requires (spec section 5) classifying every in-scope permit into one of the taxonomy classes via few-shot LLM classification, validated against 300 hand-labeled permits with per-class F1 at or above 0.80, and (spec section 6) a quantile regression model predicting P25, P50, and P75 project cost per class on a temporal train and test split.

Unlike P0, this document captures a phase that is partially complete. The classification pipeline, cost model, and CLI all run end to end today, but the specification's own acceptance gate for the classifier (hand-labeled F1) has not been cleared yet because the labels do not exist yet. This is stated plainly throughout rather than glossed over.

## End-to-end flow

1. Restrict P0's permits to the population that is actually a renovate-my-existing-home question, excluding new construction, demolition, and multifamily permits.
2. Classify every in-scope permit with a free rules-based baseline; cache results by permit number, source, and prompt version.
3. Draw a 300-permit sample stratified on the rules classifier's predicted bucket for hand-labeling.
4. Blocked: hand-label the sample, then validate the rules baseline and the LLM classifier against it.
5. Assemble cost-model features from matched permits, assessor building characteristics, lot size, a FRED-based constant-dollar deflator, and a derived review_path signal.
6. Fit a quantile gradient-boosting model per project class on a temporal split; report pinball loss and P25 to P75 coverage.
7. Serve estimates through a CLI that takes a PIN and a project class.

Data movement:

permits + permit_pin_match (P0) -> in-scope filter -> rules classifier -> permit_classification table

permit_classification -> stratified sample -> labeling_sample.csv -> pending human labels -> validate.py -> per-class F1

permit_pin_match + permit_classification + EXTR_ResBldg + EXTR_Parcel + FRED PPI -> features.py -> cost_model.py -> cost_models.joblib -> CLI

## 1. Environment and the in-scope population

All commands below run from the home_iq_app directory with .venv activated and .env exported, against the same database P0 populated.

P1 restricts to permits that are actually a renovate-my-existing-home question. IN_SCOPE_PERMIT_FILTER_SQL (homeiq_ingest/config.py) excludes permitclass = Multifamily (spec section 1 non-goals explicitly exclude commercial or multifamily property) and permittypedesc in New, Demolition, Deconstruction, Relocation (a different economic question than renovation ROI on an existing structure). This was found, not assumed: reading a random sample of what the rules classifier was dumping into other showed a large share of the full P0 permit population was out of scope by these criteria.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "
from sqlalchemy import text
from homeiq_ingest.db import get_engine
from homeiq_ingest.config import IN_SCOPE_PERMIT_FILTER_SQL
c = get_engine().connect()
total = c.execute(text('SELECT count(*) FROM permits')).scalar_one()
in_scope = c.execute(text(f'SELECT count(*) FROM permits WHERE {IN_SCOPE_PERMIT_FILTER_SQL}')).scalar_one()
print('total_permits', total)
print('in_scope_permits', in_scope)
print('excluded_pct', round(100*(1-in_scope/total),1))
c.close()
"
```

```output
total_permits 61374
in_scope_permits 35898
excluded_pct 41.5
```

## 2. Rules-based classifier

homeiq_ingest/classify_rules.py is a free, instant keyword and regex baseline over the fourteen-class taxonomy (homeiq_ingest/taxonomy.py). It is not the classifier the spec wants shipped; that is the few-shot LLM classifier in classify_llm.py, validated against hand labels. The rules baseline exists to bucket permits for stratified hand-labeling, to let feature engineering and the cost model run before an LLM key is configured, and as a sanity floor for the LLM classifier to beat.

scripts/05_classify_permits.py --backend rules has already run against the full in-scope population and cached results in permit_classification keyed by permitnum, source, and prompt_version. The query below re-derives the distribution live from that cached table rather than re-running classification, so it is safe to re-execute at any time.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "
from homeiq_ingest.db import get_engine
from homeiq_ingest.classification_store import load_classifications
df = load_classifications(get_engine(), source='rules')
vc = df['project_class'].value_counts()
print('classified_total', len(df))
for cls, n in vc.items():
    print(f'{cls:<20} {n}')
print('other_rate', round((df[\"project_class\"]==\"other\").mean(), 3))
"
```

```output
classified_total 35898
other                13621
addition_sqft        5466
basement_finish      3939
structural_work      2698
adu_detached         2202
deck_porch           1812
adu_attached         1425
kitchen_remodel      1128
siding_windows       1084
whole_house          1026
bath_remodel         757
roof                 532
garage_conversion    208
other_rate 0.379
```

## 3. Taxonomy validation before hand-labeling

Before committing a day of hand-labeling, it is worth checking whether the fixed fourteen-class taxonomy actually covers what is in the data. A high other rate from a classifier conflates two different problems: the taxonomy has no bucket for this permit, versus the classifier is simply weak. Separating them requires reading real text, not just counting.

Reading a random sample of descriptions the rules classifier bucketed as other (against the unfiltered P0 population, before the scope filter in section 1 existed) found that the taxonomy itself was basically sound, with one narrow exception: foundation and structural repair (helical piles, push piers, underpinning) is a real, economically distinct category with no bucket. It was folded into a renamed structural_work class (previously seismic_retrofit) rather than adding an entirely new class outside the spec's list. Separately, roughly a third to two-fifths of the full fetched permit population was never a valid renovation-ROI query in the first place: Multifamily permitclass, and New, Demolition, Deconstruction, or Relocation permittypedesc. That population is what section 1's IN_SCOPE_PERMIT_FILTER_SQL removes.

The query below reconstructs the same breakdown live against the current permits table, independent of the in-scope filter, to keep the finding checkable rather than asserted.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "
from sqlalchemy import text
from homeiq_ingest.db import get_engine
c = get_engine().connect()
total = c.execute(text('SELECT count(*) FROM permits')).scalar_one()
multifamily = c.execute(text(\"SELECT count(*) FROM permits WHERE permitclass = 'Multifamily'\")).scalar_one()
new_demo = c.execute(text(\"SELECT count(*) FROM permits WHERE permittypedesc IN ('New','Demolition','Deconstruction','Relocation')\")).scalar_one()
foundation_kw = c.execute(text(\"SELECT count(*) FROM permits WHERE description ~* 'foundation repair|push pier|helical pile|underpin'\")).scalar_one()
print('total_permits', total)
print('multifamily_permitclass', multifamily, round(100*multifamily/total,1), 'pct')
print('new_or_demo_permittypedesc', new_demo, round(100*new_demo/total,1), 'pct')
print('foundation_repair_keyword_hits', foundation_kw)
c.close()
"
```

```output
total_permits 61374
multifamily_permitclass 9799 16.0 pct
new_or_demo_permittypedesc 19628 32.0 pct
foundation_repair_keyword_hits 624
```

## 4. review_path: a derived feature, not a hand label

A separate taxonomy-codebook artifact flagged that permit descriptions frequently mention STFI, Subject to Field Inspection, meaning the permit is issued without plan review and verified by an inspector in the field instead. It asked whether SDCI already encodes this as a structured column before spending hand-labeling effort on it.

Checked against SDCI's actual Socrata schema for dataset 76t5-zqzr (39 fields) live: no dedicated review-path field exists. Fields that should proxy for it, totaldaysplanreview, numberreviewcycles, and planreviewcompletedate, exist but are populated in under one percent of residential permits regardless of STFI status, so they do not substitute for the text signal either; SDCI's plan-review tracking system is apparently not used for the over-the-counter track most single-family work goes through.

This is not a hand-labeling task in the first place. Whether a description contains STFI is a deterministic string match, not a judgment call like project_class. homeiq_ingest/review_path.py derives it directly as field_inspection, plan_review, or unknown, and it is wired into the cost model as a feature (homeiq_ingest/features.py, homeiq_ingest/cost_model.py). The query below re-derives the schema check and the population split live.

```bash
set -euo pipefail
echo "-- Socrata schema field names (should contain no review-path field) --"
curl -s -m 20 "https://data.seattle.gov/api/views/76t5-zqzr.json" | python3 -c "
import json, sys
cols = [c['fieldName'] for c in json.load(sys.stdin).get('columns', [])]
print('total_fields', len(cols))
hits = [c for c in cols if 'review' in c.lower() or 'stfi' in c.lower() or 'path' in c.lower()]
print('review_or_path_named_fields', hits)
"
set -a; source .env; set +a
echo "-- STFI text-signal split on the in-scope population --"
PYTHONPATH=. .venv/bin/python -c "
from sqlalchemy import text
from homeiq_ingest.db import get_engine
from homeiq_ingest.config import IN_SCOPE_PERMIT_FILTER_SQL
from homeiq_ingest.review_path import derive_review_path
import pandas as pd
c = get_engine().connect()
df = pd.read_sql(text(f'SELECT description FROM permits WHERE {IN_SCOPE_PERMIT_FILTER_SQL}'), c)
df['review_path'] = df['description'].apply(derive_review_path)
print(df['review_path'].value_counts())
c.close()
"
```

```output
-- Socrata schema field names (should contain no review-path field) --
total_fields 40
review_or_path_named_fields ['totaldaysplanreview', 'daysinitialplanreview', 'daysplanreviewcity', 'numberreviewcycles', 'initialreviewcompletedate', 'planreviewcompletedate']
-- STFI text-signal split on the in-scope population --
review_path
field_inspection    19240
plan_review         16658
Name: count, dtype: int64
```

## 5. Stratified hand-labeling sample

homeiq_ingest/sampling.py draws 300 permits stratified on the rules classifier's predicted bucket, not sampled uniformly at random, so that rare-but-real classes like garage_conversion still get enough labeled examples for a meaningful per-class F1. A uniform random sample would be dominated by whatever the rules classifier calls addition_sqft, basement_finish, or other. The sample was regenerated after the scope filter and structural_work rename in sections 1 and 3 landed, so a labeler is not scoring permits that were already out of scope.

This is real manual work the spec explicitly says not to skip, and it is not something this pipeline can do on its own. The query below reports the sample's current label completion honestly rather than assuming it is done.

```bash
set -euo pipefail; .venv/bin/python -c "
import pandas as pd
df = pd.read_csv('data/processed/labeling_sample.csv')
filled = df['human_project_class'].notna() & (df['human_project_class'] != '')
print('sample_size', len(df))
print('labeled', int(filled.sum()))
print('remaining', int((~filled).sum()))
print()
print('stratification (rules_predicted_class counts in the sample):')
print(df['rules_predicted_class'].value_counts().to_string())
"
```

```output
sample_size 300
labeled 0
remaining 300

stratification (rules_predicted_class counts in the sample):
rules_predicted_class
bath_remodel         24
other                23
structural_work      23
garage_conversion    23
addition_sqft        23
siding_windows       23
roof                 23
kitchen_remodel      23
adu_detached         23
adu_attached         23
whole_house          23
basement_finish      23
deck_porch           23
```

## 6. LLM classifier (wired, not yet run at scale)

homeiq_ingest/classify_llm.py implements few-shot classification with a strict JSON schema (response_format json_schema, strict mode) against OpenAI. The spec's own example schema is written for Claude's tool-call format; the taxonomy, prompt, and permit_classification caching layer are provider-agnostic and do not need to change to swap providers. Results are cached by permitnum, source, and prompt_version, the same as the rules backend, so a partial or interrupted run never re-pays for a permit it already classified.

scripts/05_classify_permits.py --backend llm defaults to --limit 20 rather than silently spending on the full in-scope population, because every call is a real, metered cost. No live call is made in this document for the same reason: this section only proves the classifier is correctly configured, not that it has been run. Running it for real, then validating it with scripts/09_validate_classifier.py --with-llm, is section 5's blocked dependency now that OPENAI_API_KEY is present.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "
import os
from homeiq_ingest.classify_llm import DEFAULT_MODEL, PROMPT_VERSION, _JSON_SCHEMA
print('openai_api_key_configured', bool(os.environ.get('OPENAI_API_KEY', '').strip()))
print('model', DEFAULT_MODEL)
print('prompt_version', PROMPT_VERSION)
print('schema_required_fields', _JSON_SCHEMA['schema']['required'])
print('schema_project_class_enum_size', len(_JSON_SCHEMA['schema']['properties']['project_class']['enum']))
"
```

```output
openai_api_key_configured True
model gpt-4o-mini
prompt_version v2
schema_required_fields ['project_class', 'confidence', 'scope_flags']
schema_project_class_enum_size 14
```

## 7. Zonda Cost vs. Value: obtained, deliberately not wired into code

The spec (section 6) asks for a Zonda-calibrated bias multiplier, because SDCI permit valuations are self-reported and systematically understated, plus a benchmark fallback for classes with fewer than 30 comparable permits. The actual 2025 Seattle Cost vs. Value report was obtained as a PDF (data/raw/, gitignored, never read by any code path).

Its license (page 4 of the PDF) explicitly prohibits incorporating the report, in whole or in part, into any kind of computer- or web-based application, calculator, or database without written permission from Zonda Media, which is exactly what wiring its figures into features.py or cost_model.py would be. features.ZONDA_CALIBRATION_MULTIPLIER therefore stays pinned at 1.0, and the under-30-comps benchmark fallback still returns source insufficient_data rather than a number, by deliberate choice rather than by missing data.

What the report was used for instead: a one-time manual sanity check, comparing this model's fitted median cost per class for a typical Seattle home against the report's closest comparable Seattle project types. Most classes were directionally consistent, landing at the same order of magnitude and sensibly between the report's midrange and upscale variants where this taxonomy's bucket is broader than the report's scope split. Two classes diverged enough to flag as follow-up: roof and basement_finish ran meaningfully below the comparable figure, and siding_windows ran roughly half, plausibly because that class's training population mixes in small partial-replacement permits that a fixed full-house project spec would not reflect. No number from the report appears anywhere in this repository; only this pipeline's own output is captured below.

```bash
set -euo pipefail
echo "-- proof the report never enters version control or gets parsed by any code path --"
git check-ignore -q data/raw/cvv-report-seattle-wa-2025.pdf && echo "gitignored: yes" || echo "gitignored: no"
if grep -rl "cvv-report\|costvsvalue\|pypdf\|PdfReader" homeiq_ingest/ scripts/ 2>/dev/null; then
  echo "found a reference (investigate)"
else
  echo "no code path opens, parses, or reads the report"
fi
set -a; source .env; set +a
echo "-- this pipeline's own median cost estimate per class, typical Seattle home, 2026 dollars --"
.venv/bin/python -c "
import joblib
from homeiq_ingest.db import get_engine
from homeiq_ingest.cost_model import estimate
from homeiq_ingest.features import build_training_frame

models = joblib.load('data/processed/cost_models.joblib')
df = build_training_frame(get_engine(), classification_source='rules')

for cls in ['kitchen_remodel','bath_remodel','addition_sqft','adu_attached','adu_detached','basement_finish','deck_porch','roof','siding_windows']:
    sub = df[df['project_class']==cls]
    if sub.empty or cls not in models:
        continue
    typical = {
        'sqft_living': sub['sqft_living'].median(), 'yr_built': sub['yr_built'].median(),
        'bldg_grade': sub['bldg_grade'].median(), 'condition': sub['condition'].median(),
        'bedrooms': sub['bedrooms'].median(), 'bath_full_count': sub['bath_full_count'].median(),
        'sqft_lot': sub['sqft_lot'].median(), 'permit_year': 2026,
        'zip5': sub['zip5'].mode().iloc[0] if not sub['zip5'].mode().empty else None,
        'review_path': 'plan_review',
        'layout_change': False, 'structural': False, 'plumbing_moved': False, 'sqft_added': False,
        'had_prior_permit': False,
    }
    r = estimate(models[cls], typical)
    print(f'{cls:<20} p50=\${r.p50:>10,.0f}  n={r.n_comparables}')
"
```

```output
-- proof the report never enters version control or gets parsed by any code path --
gitignored: yes
no code path opens, parses, or reads the report
-- this pipeline's own median cost estimate per class, typical Seattle home, 2026 dollars --
kitchen_remodel      p50=$    79,260  n=981
bath_remodel         p50=$    40,976  n=622
addition_sqft        p50=$   153,712  n=4531
adu_attached         p50=$   123,724  n=1146
adu_detached         p50=$   167,442  n=1556
basement_finish      p50=$    93,199  n=3471
deck_porch           p50=$    33,489  n=1505
roof                 p50=$    34,606  n=429
siding_windows       p50=$    13,325  n=788
```

## 8. Cost model: features and fit

homeiq_ingest/features.py assembles one training row per matched, cost-bearing permit: EXTR_ResBldg building characteristics (living sqft, year built, grade, condition, bedrooms, bath count), EXTR_Parcel lot size, permit year, zip5 as a Census-tract proxy (true tract needs a TIGER spatial join and a CENSUS_API_KEY the keyless ACS endpoint no longer accepts), the review_path feature from section 4, the four taxonomy scope_flags, and a FRED-based deflator to constant dollars. cost_model.py fits a HistGradientBoostingRegressor with quantile loss at P25, P50, and P75 per class with at least 30 training permits, on a temporal split, train year 2023 or earlier, test year 2024 or later, because the spec is explicit that a random split flatters a model and proves nothing.

The fit below is a live re-run against the current database, not a cached number, and can differ slightly run to run because HistGradientBoostingRegressor is not seeded to bit-identical output across environments even with random_state fixed at the estimator level; the coverage and pinball-loss direction should not change.

```bash
set -euo pipefail; set -a; source .env; set +a
.venv/bin/python -c "
from homeiq_ingest.db import get_engine
from homeiq_ingest.features import build_training_frame
from homeiq_ingest.cost_model import fit_all_class_models, MIN_SAMPLE_SIZE

df = build_training_frame(get_engine(), classification_source='rules')
print('training_rows', len(df))
models = fit_all_class_models(df)
print(f'classes_fit {len(models)} of 14 (min_n={MIN_SAMPLE_SIZE})')
print()
print(f'{\"class\":<20} {\"n_train\":>8} {\"n_test\":>7} {\"pinball@50\":>11} {\"p25-p75_coverage\":>17}')
for cls, m in models.items():
    pb50 = m.pinball_loss.get(0.5)
    cov = m.coverage
    print(f'{cls:<20} {m.n_train:>8} {m.n_test:>7} '
          f'{f\"{pb50:,.0f}\" if pb50 is not None else \"n/a\":>11} '
          f'{f\"{cov:.1%}\" if cov is not None else \"n/a\":>17}')
"
```

```output
training_rows 35640
classes_fit 13 of 14 (min_n=30)

class                 n_train  n_test  pinball@50  p25-p75_coverage
addition_sqft            4531     934      55,675             34.4%
adu_attached             1146     273      62,175             31.1%
adu_detached             1556     629      38,573             22.1%
basement_finish          3471     447      24,548             31.8%
bath_remodel              622     130      13,905             38.5%
deck_porch               1505     307      15,582             31.9%
garage_conversion         172      36      24,478             30.6%
kitchen_remodel           981     145      24,399             37.9%
other                   10149    3344      44,224             45.0%
roof                      429      66      17,687             33.3%
siding_windows            788     293       7,960             24.2%
structural_work          2123     539      10,683             33.2%
whole_house               888     136      42,292             25.0%
```

## 9. CLI deliverable

The spec's phase list (section 11) asks for a CLI that takes a PIN and a project class and prints a range. scripts/08_estimate_cost.py does this: it pulls the PIN's own ResBldg characteristics and lot size, sets review_path to unknown because a prospective query has no filed permit yet to read STFI status from, and calls the fitted model. Two real runs below: a class with a fitted model, and not_residential, which has zero training permits because P0 only ever fetched permitclassmapped equal to Residential, so the CLI correctly refuses to output a number instead of guessing.

```bash
set -euo pipefail; set -a; source .env; set +a
.venv/bin/python scripts/08_estimate_cost.py --pin 2877103801 --project-class kitchen_remodel
echo
.venv/bin/python scripts/08_estimate_cost.py --pin 2877103801 --project-class not_residential
```

```output
PIN 2877103801 — kitchen_remodel
  $46,479 - $75,948   (median $48,515)
  Derived from 981 comparable permits. source: model

PIN 2877103801 — not_residential
  Insufficient comparable permits for a model-based estimate, and no Zonda benchmark data is loaded to fall back to (see README) — refusing to fabricate a number.
```

## 10. Automated tests

The unit suite added in P1 covers taxonomy output-schema validity, every rules-classifier keyword path including the garage-conversion word-order fix, review_path's STFI variants and empty-description handling, and validate.py's shippable-versus-collapsed F1 split on synthetic labeled data. None of it requires a live database, network access, or an OpenAI key.

```bash
set -euo pipefail; .venv/bin/pytest -q -p no:cacheprovider tests | tail -5
```

```output
.....................                                                    [100%]
21 passed in 2.18s
```

## 11. P1 completion checklist

Unlike P0, this checklist is honestly mixed rather than fully satisfied.

- Taxonomy defined, validated against real data rather than assumed comprehensive, and corrected once (structural_work). Done.
- In-scope population filter found, justified against spec section 1's own non-goals, and applied everywhere permits are pulled for classification or cost training. Done.
- Rules-based classifier implemented, run against the full in-scope population, cached by permit number. Done.
- review_path derived as a feature after ruling out a structured-column shortcut with a live schema check. Done.
- LLM classifier implemented against a strict JSON schema with disk-backed caching. Wired, not yet run at scale. Blocked on a deliberate cost decision, not a technical gap.
- 300-permit stratified hand-labeling sample drawn and delivered. Not yet labeled: 0 of 300. Blocked on the user.
- Per-class F1 validation tooling built (validate.py, scripts/09_validate_classifier.py). Cannot run for real until the sample above is labeled.
- The spec's own rule, ship only classes at or above F1 0.80 and collapse the rest into other, is not yet enforced anywhere in the pipeline. Nothing currently rewrites a low-F1 class's predictions before they reach the cost model. Missing.
- Quantile cost model implemented, fit, and evaluated with a temporal split. Done, but currently trained on the noisy rules-classifier labels rather than the validated classifier the spec wants feeding it.
- CLI deliverable implemented and demonstrated on both a served estimate and a correct refusal. Done.
- P25 to P75 coverage is below the spec's 45 to 55 percent target across most classes. Reported as-is; the leading hypothesis is noisy training labels and the ZIP-proxy neighborhood feature, not yet confirmed.
- Zonda Cost vs. Value data obtained but deliberately not incorporated into the cost model due to its license; used once as a manual, unpersisted sanity check instead.

## 12. Known gaps before P2

- Hand-labeling is the actual gate. Nothing above it (F1 validation, the collapse-to-other enforcement, refitting the cost model on real labels) can proceed until it is done.
- The collapse-to-other rule needs to be built as an actual pipeline step once F1 results exist, not just identified by validate.py.
- Running the LLM classifier at full in-scope scale (35,898 permits) is a real cost decision for whoever pays the OpenAI bill, not something to trigger by default.
- Census tract and ACS median income remain a ZIP-code proxy; CENSUS_API_KEY's keyless tier no longer exists.
- The Zonda-calibrated bias multiplier stays pinned at 1.0 and the under-30-comps benchmark fallback stays unavailable, both by license, not by missing effort.
- not_residential has zero training examples in this dataset and will likely stay that way, since P0 only ever fetched Residential-mapped permits; this is an accepted limitation, not a bug to chase.
- P25 to P75 coverage should be re-checked after the LLM classifier and real hand labels replace the current rules-classifier training signal, before concluding the quantile model itself needs rework.
