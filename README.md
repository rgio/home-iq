# HomeIQ — P0/P1: Ingestion, Spatial Join & Cost Model

This covers phases P0 and P1 of the [Renovation ROI Engine spec](../homeiq-renovation-roi-spec.md):

- **P0** — ingest Seattle SDCI permits and King County Assessor parcels, and resolve every permit to exactly one PIN. Per the spec, nothing downstream works until this does.
- **P1** — classify each permit into the project taxonomy, then fit a quantile cost model per class and expose it through a CLI.

This is a separate project/repo from `../causal_analysis` (a different,
unrelated light-rail transit-effect study), though P0 reuses the KC Assessor
bulk extracts already downloaded there rather than re-fetching ~1.4GB — see
`KC_ASSESSOR_RAW_DIR` in `.env`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then edit KC_ASSESSOR_RAW_DIR if needed
```

Requires Postgres (no PostGIS extension needed — see "Why plain Postgres"
below):

```bash
bash scripts/00_setup_db.sh
```

## Run the pipeline

```bash
bash scripts/run_pipeline.sh
```

or step by step:

```bash
python scripts/01_fetch_permits.py    # Socrata -> data/processed/permits.parquet
python scripts/02_ingest.py           # load parcels + permits into Postgres
python scripts/03_run_join.py         # the actual permit -> PIN join
python scripts/04_report.py           # match-rate report + quarantine CSV
python scripts/05_classify_permits.py --backend rules   # taxonomy classification (free, instant)
python scripts/06_sample_for_labeling.py                # 300-permit stratified sample for hand-labeling
#   ... hand-label data/processed/labeling_sample.csv, then:
python scripts/09_validate_classifier.py [--with-llm]   # per-class F1 against your labels
python scripts/07_fit_cost_model.py     # quantile cost model per class + eval report
python scripts/08_estimate_cost.py --pin <PIN> --project-class <CLASS>   # P1's CLI deliverable
```

## Why plain Postgres, not PostGIS

The spec calls for PostGIS. `brew install postgis` failed building from
source under one of two parallel Homebrew installs on this machine (stale
Xcode Command Line Tools vs. the formula's build requirements); a retry
under the other (`/opt/homebrew`, where Postgres already lived) succeeded.
The schema here still doesn't use it, though, because none of the actual
point-in-polygon logic lives in SQL: `homeiq_ingest.spatial_join` does it in
GeoPandas/Shapely in Python, so PostGIS was only ever needed as a
geometry-typed, spatially-indexed store. Parcel geometry is instead
persisted as GeoJSON text in a plain Postgres column and rehydrated into a
GeoDataFrame in Python before the join. If PostGIS becomes worth adopting
later, swapping `parcels.geom_geojson TEXT` for a native `geometry` column
with a GIST index is a small, isolated change — the join logic itself
doesn't need to move.

## Pipeline design (spec §4)

**Stage 1 — spatial.** Point-in-polygon of each permit's lat/long against
parcel polygons (`homeiq_ingest/spatial_join.py`). A permit is quarantined,
never guessed at, when:

| Reason | Cause |
|---|---|
| `missing_geocode` | No lat/long on the permit record |
| `no_polygon_match` | Geocode present but lands in zero polygons (street-centerline geocode in the right-of-way) |
| `multiple_polygon_match` | Lands in more than one distinct PIN's polygon — condo/townhouse buildings where one footprint maps to many PINs, or a parcel split/merged since the permit date |

**Stage 2 — address fallback.** `missing_geocode` and `no_polygon_match`
permits get a second chance: normalize the permit's free-text address and
the parcel's structured `EXTR_ResBldg` address components to the same
canonical form, then fuzzy-match within ZIP only, accepting nothing below a
90/100 similarity score (`homeiq_ingest/address_match.py`). `multiple_polygon_match`
is *not* retried here — a closer address match doesn't resolve which of
several legitimate PINs is correct, so a guess would be worse than a
quarantine.

**On condos specifically:** `EXTR_Parcel.PresentUse` looked like a natural
way to give condos their own, more specific quarantine reason, but its
code-to-meaning mapping couldn't be verified against authoritative KC
documentation (see the gotcha below — I got it wrong twice), and an
empirical check of the actual GIS layer found only 57 parcels in all of
Seattle sharing a footprint with another PIN. Most condo buildings are
represented as a single polygon per building in this layer, not one per
unit, so a `PresentUse`-based rule wouldn't have caught most condo permits
anyway. `multiple_polygon_match` catches the cases the layer does expose;
the rest resolve to a single building/land-level PIN via ordinary spatial
match. Properly attributing a permit to a specific condo unit would need a
dedicated unit-level source — out of scope for P0.

**Acceptance criterion:** ≥90% of residential permits 2015–present resolve
to exactly one PIN. `scripts/04_report.py` prints the match rate and a
reason-by-reason quarantine breakdown, and writes the full quarantine detail
to `data/quarantine/permit_quarantine_detail.csv` for manual review.

## A data gotcha worth flagging

`EXTR_Parcel.csv`'s `PresentUse` column's code-to-meaning mapping is *not*
reliably recoverable from `EXTR_LookUp.csv` alone, and this cost two wrong
guesses before landing on "don't interpret it":

1. First guess: a `LUType` value of `2` in the lookup file has item code `2`
   labeled `"CONDOMINIUM"` — looked like a match. Wrong: applying it flagged
   76% of all parcels as condos, which is obviously implausible and would
   have quarantined the majority of permits for the wrong reason.
2. Second guess: `LUType 1` in the same file is a full WA DOR standard
   land-use code list (single family, 2-4 units, condo=14, etc.) — a much
   better structural match. Applying *that* mapping found **zero** condo
   parcels in all of Seattle, which is equally implausible.

Both guesses were caught by checking the resulting distribution against
real-world plausibility rather than trusting that a lookup-table match
implied a correct field mapping. `present_use` is now carried through as
opaque, uninterpreted data (see `homeiq_ingest/parcels.py`); resolving its
real meaning needs KC's official schema documentation, not inference from
this lookup file.

## Taxonomy classification (spec §5)

Two classifier implementations behind the same interface (`classify_batch(permits_df) -> df[permitnum, project_class, confidence, scope_flags]`), cached by `(permitnum, source, prompt_version)` in the `permit_classification` table:

- **`homeiq_ingest/classify_rules.py`** — free, instant, zero-config keyword/regex baseline. Not the classifier the spec wants shipped; it exists to (a) bucket permits for the stratified hand-labeling sample so rare classes aren't crowded out, (b) let the rest of the pipeline (features, cost model) run before an LLM key is configured, and (c) give the LLM classifier something to beat. On the in-scope permit set (see "Validating the taxonomy" below) it lands ~38% in `other` — expected for a keyword matcher, not a target to hit.
- **`homeiq_ingest/classify_llm.py`** — few-shot classification with a strict JSON schema (`response_format=json_schema`, strict mode) against OpenAI (the spec's own example schema is written for Claude's tool-call format; the taxonomy/prompt/caching layer is provider-agnostic). Requires `OPENAI_API_KEY` in `.env`. `scripts/05_classify_permits.py --backend llm` defaults to `--limit 20` rather than silently spending on the full ~60k-permit set — raise it deliberately.

**Hand-labeling (the 300-permit F1 check):** `scripts/06_sample_for_labeling.py` pulls a sample stratified on the *rules* classifier's predicted bucket (23/class across the 13 classes actually present in Seattle residential permits), so rare-but-real classes like `garage_conversion` get enough labeled examples for a meaningful F1 — a random sample would be dominated by whatever's most common. This is real manual work the spec explicitly says not to skip; nothing here fabricates it. Once `data/processed/labeling_sample.csv`'s `human_project_class`/`human_scope_flags` columns are filled in, `scripts/09_validate_classifier.py` reports per-class precision/recall/F1 and which classes clear the `MIN_SHIPPABLE_F1 = 0.80` bar (`homeiq_ingest/validate.py`); classes that don't collapse into `other` per spec §5.

## Cost model (spec §6)

`homeiq_ingest/features.py` assembles training rows from matched permits (P0) + their taxonomy classification (P1) + `EXTR_ResBldg` building characteristics (sqft, year built, grade, condition, bedrooms) + `EXTR_Parcel` lot size. `homeiq_ingest/cost_model.py` fits a `HistGradientBoostingRegressor` with quantile loss at P25/P50/P75 per project class with ≥30 training permits, on a **temporal** train/test split (train ≤2023, test ≥2024 — the spec is explicit that a random split "will flatter you and mean nothing"). `scripts/07_fit_cost_model.py` reports pinball loss and P25–P75 coverage per class; `scripts/08_estimate_cost.py --pin <PIN> --project-class <CLASS>` is P1's CLI deliverable.

**Two gaps vs. the spec, both because the underlying data needs something this environment doesn't have — not glossed over:**

- **Neighborhood control is ZIP, not Census tract.** True tract needs a spatial join against Census TIGER geometry; the ACS tract-median-income feature also needs a `CENSUS_API_KEY` — the endpoint's keyless tier no longer exists (confirmed: it now redirects to a "Missing Key" page, this wasn't always the case).
- **The Zonda-calibrated bias multiplier (spec §6: permit valuations "systematically understated") is a visibly-named constant pinned at `1.0`** (`features.ZONDA_CALIBRATION_MULTIPLIER`), and the **<30-comps benchmark fallback** (`cost_model.estimate()` returning `source="insufficient_data"`) has no benchmark data to fall back to. Not for lack of the data this time: the actual 2025 Seattle Cost vs. Value report was obtained (`data/raw/`, gitignored). It's deliberately not wired into either of these, because its license (page 4 of the PDF) explicitly prohibits incorporating the report "in whole or part... into any kind of computer- or web-based application, calculator, or database" without written permission from Zonda Media — which this pipeline is. Used instead as a one-time manual sanity check on the fitted model (see below), never read or stored by any code path. The deflation-to-constant-dollars piece *is* wired in and unaffected by any of this: `homeiq_ingest/fred.py` pulls FRED series `WPUSI012011` (PPI, Inputs to Construction) via its keyless public CSV export — a different, narrower thing than the Zonda bias correction and shouldn't be confused with it.

**Manual sanity check against the Zonda report** (done once, by hand, outside the codebase — no file in this repo contains Zonda's figures): comparing the fitted model's median cost per class (for a typical-home feature vector) against the report's closest comparable Seattle project types, most classes were directionally consistent — same order of magnitude, and sitting sensibly between Zonda's "midrange" and "upscale" variants where our taxonomy bucket is broader than Zonda's scope split (kitchen, bath, additions, ADUs, decks). Two classes diverged enough to flag as follow-up: `roof` and `basement_finish` ran 20–35% below the comparable Zonda figure, and `siding_windows` ran roughly half — worth checking whether that class's training population is dominated by small partial-replacement permits, which Zonda's fixed full-house project spec wouldn't reflect at all.

**Where it currently falls short of the spec's own bar:** P25–P75 coverage on the temporal holdout is running 20–39% across classes (`other` hits 45%), below the spec's 45–55% target (`scripts/07_fit_cost_model.py`'s output). Reporting this as-is rather than tuning until the number looks right. Two of the three inputs upstream of it are known-noisy right now: the model is trained on the *rules* classifier's labels (not yet the LLM classifier, pending the hand-label validation above and an API key), and the ZIP-proxy/no-tract-income feature set is coarser than the spec's. Worth re-checking coverage after both are resolved before concluding the model itself needs rework. (Pinball loss did improve substantially after the scope filter below was applied — e.g. `addition_sqft` roughly halved — so the filter was a real fix, not just cosmetic.)

## Validating the taxonomy before hand-labeling

Before committing to hand-labeling, it's worth checking whether the spec's 14 fixed classes actually cover what's in the data — a high "other" rate from a classifier conflates two different problems ("the taxonomy has no bucket for this" vs. "the classifier is dumb"), so the check has to separate them. Read a random sample of what the rules classifier was bucketing as `other` (`git log`-free — just eyeballing 80 real descriptions) and found:

- **The taxonomy itself was basically fine** — no large natural category was missing, with one narrow exception: **foundation/structural repair** (helical piles, push piers, underpinning), ~576 permits, a real and economically distinct category. Resolved by folding it into a renamed `structural_work` class (was `seismic_retrofit`) rather than adding an entirely new one.
- **~35% of the entire fetched permit population was never a valid "renovation ROI" query in the first place**: `permitclass = 'Multifamily'` (16% — spec §1's own non-goals exclude commercial/multifamily property), `permittypedesc = 'New'` (17% — brand-new construction is a different economic question than renovating an existing home), and `Demolition`/`Deconstruction`/`Relocation` (8%). These were inflating the "other" bucket and would have diluted both the hand-labeling sample and the cost model's training signal. `IN_SCOPE_PERMIT_FILTER_SQL` (`homeiq_ingest/config.py`) excludes them everywhere permits are pulled for classification, labeling, or cost-model training.

Net effect after filtering: the rules classifier's `other` rate dropped from 55% to 38% on the same population, and cost-model pinball loss improved substantially (above) — both consistent with "this was real noise," not just a smaller sample.

## `review_path`: a feature, not a label (found via the codebook artifact)

Separately, the taxonomy codebook flagged that permit descriptions frequently mention "STFI" (Subject to Field Inspection — issued without plan review) and asked whether SDCI already encodes this as a structured column before hand-labeling it. Checked against SDCI's actual Socrata schema (39 fields) — no dedicated field exists, and the fields that *should* proxy for it (`totaldaysplanreview`, `numberreviewcycles`, `planreviewcompletedate`) turned out to be populated in <1% of residential permits regardless of STFI status, so they can't substitute either.

But this isn't a hand-labeling task in the first place: "does the description contain STFI" is a deterministic string match, not a judgment call like `project_class`. `homeiq_ingest/review_path.py` derives it directly (`field_inspection` / `plan_review` / `unknown`) and it's now a cost-model feature (`homeiq_ingest/features.py`, `homeiq_ingest/cost_model.py`) — it splits ~54% of the in-scope population and improved pinball loss modestly across most classes when re-fit, consistent with the codebook's prediction that it's "orthogonal to everything the description says about scope."

## Licensing / privacy (spec §3)

The Assessor bulk download is gated on acknowledging RCW 42.56.070(9), which
prohibits using lists of individuals for commercial purposes.
`homeiq_ingest/privacy.py` strips `BuyerName`/`SellerName` from every
DataFrame before it's persisted or leaves the ingestion layer
(`load.load_permits` calls this automatically). Raw data directories
(`data/raw`, `data/processed`, `data/quarantine`) are gitignored outright.

## Tests

```bash
pytest
```

Covers address normalization/matching and every join failure mode in
`spatial_join.py` against synthetic fixtures — no DB or network required.
Nothing here needs live Postgres to run.

## Design note for P2 (not yet built): a bad-control problem in spec §7's matching keys

Spec §7 matches treated parcels to controls on "tract, sqft band, year-built band, grade, condition, and holding period," sourced from EXTR_ResBldg. That's fine for characteristics fixed at construction, but `SqFtTotLiving`, `BldgGrade`, and `Condition` are exactly what a lot of permitted work *changes* — an addition, a DADU, a basement finish, or a garage conversion moves square footage; grade/condition can shift with any project substantial enough to catch an assessor's eye at reassessment. EXTR_ResBldg is a single current-vintage snapshot, so for any parcel with a repeat sale bracketing one of these projects, matching on its *current* sqft/grade/condition means matching on the post-treatment state — a classic bad control (Angrist & Pischke): conditioning on a variable treatment itself moves absorbs some or all of the effect being estimated. Vivid case: a parcel that converts a garage into a garage-plus-ADU is a different structure after the permit than the one that sold at t0; any post-issuance characteristic snapshot describes a property that doesn't resemble the t0 sale.

**Checked and confirmed:** King County Assessor's bulk download portal (`info.kingcounty.gov/assessor/DataDownload`) publishes only a single rolling current extract, not selectable annual vintages — third parties who have historical snapshots got them by manually saving periodic downloads over time, which this project doesn't have. So there's no way to pin building characteristics to the sale year from this source.

**Fix for P2:** restrict match/control keys to characteristics treatment can't plausibly move — `year_built`, lot size (`SqFtLot`, fixed unless the parcel itself splits/merges — P0's `multiple_polygon_match` quarantine already flags that case), tract/location, and holding period. Drop `sqft band` and `grade`/`condition` from the matching keys entirely rather than only for the classes that obviously add square footage — an assessor's condition/grade rating can move even for a treatment as modest as new siding or a roof. This should also be added to spec §7's own "known threats to identification" list when P2's writeup is produced; it isn't in the spec's current list (selection, unpermitted-work-in-controls, bundled improvements, issuance-vs-completion date) but is a distinct and probably larger threat than several of those for the classes that add space.

## What's not in P0/P1

Project taxonomy classification is P1 (this repo); the value model (P2), the
MCP servers and orchestrator (P3), and RAG (P4) are out of scope for this
repo so far — see the top-level spec's build-phases section.
