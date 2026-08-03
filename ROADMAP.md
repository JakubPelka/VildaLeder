# VildaLeder roadmap

This roadmap takes VildaLeder from a documented concept to an anonymous web MVP,
then to a scalable Sweden-wide service and, only after validation, native mobile
applications with optional paid features.

The phases are ordered by dependency rather than by calendar date. A phase is
complete only when its exit criteria are met.

## Current implementation status — August 2026

The repository now contains a working vertical slice spanning parts of phases
0–4:

- all 64 named OSM hiking/foot route relations discovered in Halmstads kommun;
- a Halland-wide spatial catalog built municipality by municipality, including
  175 named OSM hiking/foot routes and 213 current nature reserves from the
  authoritative Naturvårdsregistret;
- reserve analysis geometries covering the complete protected polygon plus a
  200-metre outward buffer, and cross-municipality membership-aware filters;
- metric 200-metre corridors generated in SWEREF 99 TM;
- a checked-in ten-year Halland snapshot covering all 175 trails and 213 nature
  reserves, with 1,300,530 canonical Artportalen/SOS observations and 1,907,193
  trail/reserve matches, with complete pagination beyond the 10,000-result edge;
- a second deduplicated species-map export of those 1,300,530 SOS points, using
  compact feature ordinals plus 256 taxon buckets and small time partitions so
  map counts remain responsive without losing buffer-filter semantics;
- an experimental ten-year Halland Skandobs adapter: 672 public predator reports
  checked, 74 public wolf/lynx reports retained, and 90 trail/reserve matches,
  with a strict public-field whitelist and graceful stale-snapshot fallback;
- lazy route/time point partitions and a daily aggregate index, so counts and
  species rankings respond to all date presets and custom ranges without loading
  every point at startup;
- Red List category ordering from the SOS observation payload;
- map-level Red List category counts, zero-category suppression, and per-class
  visibility toggles;
- numbered map clusters for overlapping coordinates and a selected-place,
  paginated observation table with sortable columns and row-to-map navigation;
- map hover labels and click cards with authoritative OSM or Skyddad natur
  source links for every selectable trail and reserve;
- a keyboard- and pointer-accessible splitter that lets the user resize the map
  and observation table while retaining the chosen ratio locally;
- trail-first and species-first (`havsörn` included) journeys;
- species-first maps backed by a bucketed SOS/Skandobs point index that
  immediately show every available, deduplicated public point for the chosen
  species intersecting the active trail/reserve buffers before one object is
  selected, while deliberately excluding observations outside tourist places;
- English, Swedish, and Polish interface dictionaries;
- a place-type filter and a full reset control for filters, searches, selected
  feature, dates, Red List toggles, and map extent;
- day, month, quarter, year, and inclusive custom date filters;
- custom date controls that remain hidden until selected and automatically
  activate the custom preset when either boundary changes;
- opt-in map geolocation with an accuracy area and two-second marker refresh;
- browser, geometry, catalog-integrity, privacy, and static-asset checks;
- a PostgreSQL 18/PostGIS 3.6 target schema, repeatable migrations, an idempotent
  snapshot importer, source-aware multilingual taxon names, and integration CI;
- CI and GitHub Pages deployment workflows;
- an always-on-server systemd routine at 03:00 Europe/Stockholm that refreshes
  spatial features, incrementally reconciles SOS and Skandobs, verifies and
  exports an all-or-nothing snapshot, pushes it to `main`, and causes open
  clients to reload after the deployed generation marker changes.

This is a functional pilot, not completion of the phases. The most important
open gates are explicit Red List 2025 provenance, multilingual taxon names,
shareable URL state, production map tiles, licensing review, national source
ingestion, and an
HTTPS serving API over the PostGIS store described in Phase 4.

## Delivery principles

- Start with a narrow geographic pilot and real data.
- Prefer measurable baselines over opaque recommendation algorithms.
- Keep trail, observation, taxonomy, and administrative-boundary sources behind
  explicit adapters.
- Never put upstream API secrets in browser or mobile bundles.
- Cache expensive spatial results and retain provenance.
- Treat sensitive species and licence compliance as launch blockers, not polish.
- Build the anonymous web experience before accounts, subscriptions, or native
  packaging.

## Phase 0 — Feasibility and source contracts

**Goal:** prove that marked trails, spatial buffers, recent observations,
taxonomy, and conservation status can be joined legally and reproducibly.

### Work

- Select a representative pilot area; assess Hallands län as the default
  candidate.
- Extract `route=hiking` and `route=foot` relations from OSM for the pilot.
- Measure route completeness:
  - valid and connected geometries;
  - missing names or references;
  - duplicate/superroute relations;
  - multilingual tags;
  - route length and geometry size.
- Compare trail acquisition strategies:
  - Overpass for discovery and small refreshes;
  - regional OSM extracts for deterministic batch processing;
  - a hosted or self-managed route dataset if national refreshes outgrow public
    Overpass capacity.
- Obtain access to SLU’s “Species Observations – multiple data resources”
  product and document API-key handling.
- Build equivalent pilot queries against:
  - SLU Species Observation System / Artportalen;
  - GBIF Occurrence Search.
- Confirm polygon, date, taxon, pagination, aggregation, and result-limit
  behaviour with recorded fixtures.
- Measure overlap, freshness, duplicate records, taxon identifiers, coordinate
  precision, and source-specific fields.
- Verify which API provides the current Swedish Red List 2025 assessment and how
  its version is represented; do not silently fall back to the older 2020 list.
- Validate Dyntaxa name search for scientific, Swedish, English, Polish, and
  synonym coverage.
- Evaluate SCB’s simplified `län`/`kommun` boundaries for UI filtering and select
  an analysis-grade source if accurate spatial assignment is required.
- Document licences, attribution, citation, caching, retention, and redistribution
  obligations for every source and for derived corridor datasets.
- Define a sensitive-species policy using only public, source-approved geometry.
- Make the UI explain that protected records may be withheld or spatially
  generalised and that absence of public points is not evidence of absence.
- Run Skandobs as a clearly labelled experimental large-carnivore evidence
  source through its anonymous web API. Keep the adapter isolated, retain the
  last good snapshot on failure, store no personal response fields, and treat
  written stability, attribution, caching, and redistribution terms as a gate
  for dependable commercial use rather than for the open pilot experiment.
- Decide whether the 200-metre corridor is fixed, configurable, or adjusted by
  source coordinate uncertainty.

### Deliverables

- `docs/discovery/data-source-evaluation.md`
- `docs/contracts/trail-source.md`
- `docs/contracts/observation-source.md`
- `docs/contracts/taxonomy-and-red-list.md`
- recorded API fixtures without credentials or protected data;
- a small reproducible pilot dataset;
- architecture decision records for data access, boundaries, basemap, and cache.

### Exit criteria

- At least 20 representative trails can be reconstructed and identified.
- A 200-metre trail corridor returns reproducible observations for known test
  cases.
- Source records can be deduplicated without collapsing distinct observations.
- Red List category and assessment year can be attached to returned taxa.
- No secret or protected location is required by the public client.
- The data and licensing model permits the intended public MVP.

## Phase 1 — Web foundation and walking skeleton

**Goal:** deploy the smallest end-to-end application to GitHub Pages.

### Work

- Select and record the frontend stack; the current candidate is React,
  TypeScript, and Vite.
- Create a responsive application shell with map, result drawer, loading, empty,
  and error states.
- Add MapLibre GL JS and select a basemap provider with suitable production
  terms, attribution, capacity, and a migration path for mobile/offline use.
- Establish source modules:
  - `trails`;
  - `observations`;
  - `taxonomy`;
  - `red-list`;
  - `administrative-areas`.
- Implement runtime validation for downloaded data contracts.
- Add locales and locale tests for `en`, `sv`, and `pl`.
- Add URL-addressable state for language, viewport, selected area, selected
  trail/species, and time range.
- Add accessibility foundations: keyboard navigation, focus management, colour
  contrast, non-colour status cues, and screen-reader labels.
- Add unit tests, formatting, linting, type checking, build verification, and a
  GitHub Pages deployment workflow.
- Publish source attribution and a visible data-version indicator.

### Exit criteria

- The application loads from its GitHub Pages project URL without console errors.
- A sample trail can be selected on the map and from a list.
- A fixture species list renders in all three languages.
- Direct links restore the same selected view.
- CI blocks a broken build, missing locale key, or invalid data contract.

## Phase 2 — Trail-first pilot MVP

**Goal:** answer “what has recently been observed along this trail?” with live or
recently cached public data.

### Work

- Ingest and normalise pilot-area OSM route relations.
- Generate and version 200-metre trail corridors in a metric projection.
- Assign trails to `län` and `kommun`, including routes that cross boundaries.
- Build map and list filters for administrative area, route network, and text.
- Implement trail name/ref search and deterministic fallback labels.
- Implement trail selection, fit-to-route, hover/focus synchronisation, and a
  route detail panel.
- Add time presets:
  - rolling 24 hours;
  - rolling 30 days;
  - rolling 90 days;
  - rolling 365 days;
  - custom inclusive dates.
- Query observations within the actual corridor polygon.
- Aggregate by canonical taxon and show:
  - common and scientific names;
  - Red List category and assessment year;
  - count and most recent observation;
  - source and validation/precision information;
  - links back to the authoritative source where possible.
- Support sorting by Red List category, recency, count, and name.
- Add explicit states for no observations, incomplete geometry, source outage,
  partial results, and result truncation.
- Cache by trail version, corridor width, source, filter set, time range, and data
  version.

### Exit criteria

- All valid pilot trails can be opened from the map, list, and search.
- Boundary cases prove that observations just inside/outside 200 metres are
  classified correctly.
- Date presets and custom dates produce testable inclusive ranges.
- Duplicate source records do not inflate displayed counts.
- Red List sorting is deterministic and explained in the UI.
- The application does not reveal non-public locations.
- A source outage degrades gracefully without breaking trail browsing.

## Phase 3 — Species-first recommendations

**Goal:** answer “which trails in this area have the strongest recent evidence
for my chosen species?”

### Work

- Implement taxon autocomplete using stable IDs, accepted scientific names,
  synonyms, and available vernacular names.
- Make autocomplete accent-insensitive and show the scientific name beside every
  vernacular match.
- Add a curated, source-labelled alias layer for missing English and Polish names.
- Filter candidates by Sweden, `län`, or `kommun` before spatial scoring.
- Compute the transparent baseline per trail:
  - deduplicated observation count;
  - observations per kilometre;
  - distinct observation dates;
  - most recent observation;
  - trail length and corridor area;
  - coordinate/validation quality summaries.
- Display the ranking inputs, not only the rank.
- Add comparable result cards and select/zoom behaviour.
- Add seasonal and reporting-effort warnings.
- Add an optional multi-species journey with two or three taxon chips. Candidate
  trails and reserves must have evidence for every selected taxon in the active
  period (`AND`); never silently broaden this to `OR`.
- Normalize each selected taxon's evidence within the current candidate area
  using log-scaled deduplicated counts, distinct observation days, and recency.
  Combine the per-taxon values with a geometric or harmonic mean plus a weakest-
  taxon penalty, so one abundant species cannot conceal poor evidence for
  another. Show the raw count, distinct days, and latest date for every taxon and
  describe the combined value as a match score, not a sighting probability.
- Test rankings with representative species, including havsörn and taxa with
  sparse, common, sensitive, and multilingual records.

### Exit criteria

- A user can search by scientific or supported vernacular name.
- The same taxon ID is selected regardless of which supported alias was typed.
- Municipality/county filters alter the candidate route set predictably.
- Ranking is deterministic for a frozen data fixture.
- Each suggestion explains the observations supporting it.
- The UI never states or implies that a high rank guarantees a sighting.

## Phase 4 — Sweden-wide scale and data quality

**Goal:** expand from the pilot without overwhelming browsers or upstream public
services.

### Work

- Introduce one normalised Sweden-wide PostgreSQL/PostGIS observation store
  instead of copying the same source observation into every
  overlapping trail or reserve dataset.
- Synchronise the local observation store incrementally at least every 24 hours:
  ingest new and changed source records, recheck a rolling correction window,
  retain source identifiers and provenance, and run a less frequent full
  reconciliation.
- Keep observations, trail lines and 200-metre corridors, nature-reserve
  boundaries, taxonomy, and administrative areas as separate spatial entities;
  compute and version their intersections in the local data platform.
- Ingest authoritative nature-reserve boundaries and multilingual names. Add an
  optional reserve-name filter and a reserve-first journey: select a reserve to
  inspect recent species and intersecting walks, or select a species to rank
  reserves as well as trails across Sweden.
- Evaluate Naturvårdsverket's **Leder och anordningar** as the primary source of
  maintained public walking trails once its metadata catalogue is available
  again, with OSM retained as a complementary source for wider community-mapped
  coverage. Until the adapter can be verified, state clearly in the UI that the
  OSM trail catalogue may be incomplete or outdated.
- Normalise Naturvårdsverket and OSM trails into canonical features without
  discarding either source record. Generate duplicate candidates using provider
  IDs, normalised name/operator/municipality, endpoint proximity, and buffered
  line overlap or a measured line-distance metric. Automatically merge only
  strong matches, retain source IDs and geometries as provenance, and queue
  ambiguous candidates for review; a shared name alone must never merge routes.
- Materialise daily aggregates for `taxon × trail/reserve × date` so time-range
  counts, Sweden-wide species rankings, and optional `län`/`kommun` filters do
  not require repeated upstream API calls or browser downloads of raw points.
- Use resumable feature/year SOS windows for the Halland bootstrap and record
  explicit per-feature coverage in PostGIS. Static pilot exports must include
  only complete windows; interrupted or partial ingestion must never look like
  a valid zero-observation result.
- Add a GBIF enrichment adapter after the SOS baseline. Exclude the complete
  Artportalen GBIF dataset (`38b4c89f-584c-41bb-bd8f-cd1def33e92f`) at query or
  asynchronous-download time, because those records mirror the SOS source that
  is already canonical in VildaLeder.
- Retain `gbifID`, `datasetKey`, `occurrenceID`, `catalogNumber`, licence, and
  publisher for every GBIF source record. Attach a second source record to an
  existing canonical observation only when a stable shared identifier proves
  equivalence; keep same-taxon/date/coordinate fingerprints as review signals,
  not automatic merges that could collapse legitimate group observations.
- Pilot GBIF with bounded Halland/date queries, then use authenticated,
  asynchronous GBIF downloads for national backfills. The occurrence-search API
  is suitable for interactive tests but pages at 300 records and has a hard
  100,000-record query ceiling.
- Keep the national bootstrap in an isolated `vildaleder_sweden` database until
  coverage, storage, query latency, and daily correction handling have been
  verified. Seed it with the validated Halland database, checkpoint every
  feature/year window, and switch a future API DSN rather than exposing partial
  national data through the static Halland client.
- Enrich SOS/Dyntaxa taxa with cached GBIF vernacular names for `en` and `pl`
  while preserving Swedish names and scientific names. Treat GBIF as the source
  of those name assertions, not as authority over Swedish Red List status.
- Fetch raw point evidence from the VildaLeder service only after a user selects
  a trail, reserve, or species result; continue to expose authoritative source
  links and data-quality context.
- Treat permission to retain, cache, and redistribute a nationwide SOS/GBIF
  copy as an architecture gate. Document retention limits and the policy for
  protected, obscured, corrected, and deleted observations before backfilling.
- Partition trails and derived data by stable geographic cells or administrative
  areas.
- Replace large GeoJSON payloads with vector tiles, PMTiles, or another measured
  distribution format.
- Incrementally refresh changed OSM relations instead of rebuilding everything.
- Introduce scheduled observation aggregation with retry, backoff, source quotas,
  and stale-cache serving.
- Separate raw source cache, normalised observations, and user-facing aggregates.
- Publish data manifests with build time, source versions, coverage, failures,
  and checksums.
- Add geometry QA for gaps, loops, reversed segments, duplicated members,
  superroutes, and unrealistic lengths.
- Add monitoring for API drift, route-count anomalies, empty partitions, stale
  data, and red-list version changes.
- Benchmark initial load, map interaction, trail selection, and species ranking on
  mobile-class hardware.
- Decide when GitHub-hosted artifacts are no longer appropriate and migrate data
  delivery before hitting Pages/repository limits.

### Exit criteria

- Sweden-wide browsing stays within the agreed performance budget.
- Refresh jobs resume safely after partial failure.
- Every displayed aggregate links to a data manifest and provenance.
- Upstream rate limiting cannot trigger an uncontrolled retry storm.
- National data volume remains within documented hosting and cost budgets.
- A daily incremental sync updates counts without rebuilding every trail and
  reserve from the upstream APIs, and a failed sync leaves the last complete
  snapshot available.
- The same observation is stored once and can support both trail and
  nature-reserve discovery without multiplying raw storage by overlap count.

## Phase 5 — Public web beta and PWA evaluation

**Goal:** make the anonymous web experience reliable enough for real users.

### Work

- Conduct usability testing in English, Swedish, and Polish.
- Add privacy, terms, data-source, attribution, correction, and contact pages.
- Add a clear feedback flow for broken routes, mistranslations, and suspect data.
- Add privacy-preserving product analytics only after an explicit decision and
  documentation.
- Evaluate installable PWA behaviour and limited offline access for product-owned
  data.
- Do not offer offline OSM standard tiles; choose a provider or self-hosted tile
  path whose licence and policy explicitly permit offline use.
- Add browser and device compatibility coverage.
- Run security, dependency, accessibility, and performance reviews.
- Define public uptime/freshness expectations and incident communication.

### Exit criteria

- Core journeys pass usability testing in all launch languages.
- Legal attribution and sensitive-data review are complete.
- Accessibility review finds no launch-blocking issue.
- Error monitoring and data-freshness monitoring are operational.
- The team has evidence that users return to either core journey.

## Phase 6 — Mobile product discovery

**Goal:** determine whether native apps add enough value to justify separate
distribution, operations, and commercial complexity.

### Questions to validate

- Which features truly need native capabilities: background GPS, offline maps,
  route progress, notifications, camera, or saved searches?
- Can a PWA satisfy the useful subset first?
- Which web domain modules can be shared safely with iOS and Android?
- Does MapLibre Native or a React Native mapping stack meet performance and
  offline requirements?
- What data and map-provider licences permit offline packs and commercial use?
- What remains free, and what could justify a subscription?
- Are accounts necessary for sync, purchases, alerts, or saved lists?
- How will Apple/Google in-app purchase rules, entitlement restoration, privacy,
  and account deletion be handled?

### Deliverables

- mobile architecture spike on both iOS and Android;
- offline-map and offline-observation proof of concept;
- product/price research and free-versus-paid feature proposal;
- backend, authentication, entitlement, privacy, and support cost model;
- app-store compliance checklist.

### Exit criteria

- Native-specific value is demonstrated, not assumed.
- A sustainable map/data licensing and cost model exists.
- The account and subscription model has a documented privacy and support plan.
- GitHub Pages has been removed from any commercial backend responsibility.

## Phase 7 — Native apps and optional subscriptions

**Goal:** release trustworthy iOS and Android products without weakening the open
web experience.

### Candidate free features

- trail and species discovery;
- current public observations;
- basic filters and route details;
- shareable links.

### Candidate paid features to validate

- offline regional maps and trail datasets;
- saved trails/species with cross-device sync;
- configurable observation alerts;
- advanced historical comparisons and filters;
- route collections or trip planning.

Paid features are hypotheses, not commitments. Public-source data must not be
misrepresented as proprietary, and subscription value must come from product
functionality, reliability, convenience, and operations.

### Exit criteria

- App Store and Play Store production releases pass review.
- Purchases, restoration, cancellation, and account deletion are tested.
- Free web functionality remains available without forced registration.
- Operational monitoring, support, backups, and incident response cover mobile
  and backend services.

## Cross-cutting backlog

- Route surface, accessibility, elevation, difficulty, and public-transport
  access.
- Seasonal closures, local restrictions, weather, fire risk, and hunting notices.
- Observation photos where source licences permit display.
- Saved searches and notifications.
- Compare multiple trails.
- Shareable trip cards.
- Community route-quality feedback without editing OSM implicitly.
- Additional interface languages and vernacular-name sources.
- Transparent personalisation that does not hide the baseline ranking.

## Explicitly out of scope for the first MVP

- User accounts and social features.
- Reporting observations back to Artportalen/GBIF.
- Navigation or safety-critical turn-by-turn routing.
- Guaranteed sightings or habitat-suitability predictions.
- Revealing protected or obscured species locations.
- Commercial subscriptions.
- Native iOS/Android binaries.
- Nationwide offline basemap downloads.

## Principal risks

| Risk | Early mitigation |
|---|---|
| OSM route relations are incomplete or fragmented | Pilot QA, source-version tracking, deterministic exclusion reasons |
| Public Overpass instances do not scale nationally | Regional extracts, incremental ingest, caching, controlled refresh jobs |
| SLU API key cannot be exposed on GitHub Pages | Keep it in the ignored local-server environment/key file or a future backend secret store; never ship it to the client |
| GBIF and SOS overlap or disagree | Exclude the Artportalen GBIF dataset, preserve all remaining source IDs, merge only on stable shared identifiers, and display provenance |
| Observation counts mostly measure observer effort | Show raw inputs, per-km and distinct-date context, avoid probability claims |
| Sensitive observations could attract disturbance | Use public/source-approved geometry only; suppress or generalise presentation when required |
| Skandobs exposes a web API without a clear public integration contract | Keep ingestion best-effort and source-isolated, retain the last good snapshot, export only a privacy whitelist, and obtain written terms before dependable commercial use |
| Red List versions or taxon concepts change | Store assessment year and source taxon ID; version mappings and invalidate caches |
| Multilingual common-name coverage is incomplete | Scientific-name fallback and curated, source-labelled alias catalogue |
| Static hosting/data limits are exceeded | Geographic partitioning, compact formats, measured budgets, planned hosting migration |
| Future monetisation conflicts with hosting or data terms | Legal/licensing gate before mobile subscriptions; move commercial services off Pages |

## Definition of the web MVP

The web MVP is complete when an anonymous user can, in English, Swedish, or
Polish:

1. select a Swedish pilot municipality or county;
2. browse and search marked OSM trails;
3. open a trail and see observations within its versioned 200-metre corridor for
   a selected time range;
4. sort species by Red List category, recency, count, or name;
5. search a species by scientific or supported vernacular name;
6. receive explainable trail suggestions for that species;
7. see source, date, data quality, coverage, and attribution information;
8. use a shareable URL without creating an account;
9. encounter no protected coordinates, embedded secrets, or misleading sighting
   guarantees.
