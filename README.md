# VildaLeder

**Find wildlife by trail — or find a trail by wildlife.**

VildaLeder is a Sweden-first discovery tool that connects marked walking and
hiking routes with recent biodiversity observations. The first product will be
an open, multilingual web application published through GitHub Pages. A native
iOS and Android application, accounts, subscriptions, and paid features are
possible later, after the public web MVP has demonstrated that the underlying
data and ranking are useful.

> Project status: functional Halmstad pilot. The web MVP runs locally and is
> prepared for deployment at
> [jakubpelka.github.io/VildaLeder](https://jakubpelka.github.io/VildaLeder/).

## Product vision

Most nature-observation services begin with a map or a species database.
VildaLeder begins with a walk a person can actually take.

The product has two primary entry points:

### 1. Trail first

“I want to walk this trail. What has been observed nearby recently?”

1. Browse or search marked trails on a map.
2. Filter the map by Swedish county (`län`) and municipality (`kommun`).
3. Select a trail on the map or from the results list.
4. Zoom to the complete route.
5. Query public nature observations inside an approximately 200-metre corridor
   around the trail.
6. Show recently observed species, prioritised by Swedish Red List category and
   accompanied by observation date, count, source, and data-quality context.
7. Change the time range: day, month, quarter, year, or custom dates.

### 2. Species first

“I want to look for a species such as havsörn. Which trails give me the best
chance of an interesting walk?”

1. Search by scientific name or a supported vernacular name.
2. Choose Sweden, a county, or a municipality.
3. Choose a time range.
4. Rank trails by observations of the selected species within their trail
   corridors.
5. Open a suggested trail to inspect the route, observation evidence, and the
   assumptions behind its ranking.

Observation count is evidence of past reporting, not a promise that a species
will be present. The interface must make that distinction clear.

## Initial scope

- Geography: Sweden.
- Trails: identifiable walking and hiking route relations from OpenStreetMap,
  initially `type=route` with `route=hiking` or `route=foot`.
- Observation corridor: 200 metres on each side of the trail by default. The
  exact spatial method and whether the width becomes configurable will be
  validated during discovery.
- Nature observations: public, georeferenced records from the SLU Species
  Observation System (including Artportalen) and/or GBIF.
- Conservation context: the current Swedish Red List and related conservation
  information from SLU Artdatabanken.
- Languages at launch: English, Swedish, and Polish.
- Access: public and anonymous; no account required.
- Delivery: responsive web application on GitHub Pages.

The current pilot discovers all 64 named OSM `route=hiking|foot` relations in
Halmstads kommun, Hallands län. It contains a generated ten-year snapshot of
public Artportalen observations inside real 200-metre trail corridors. Area
filters are optional: the unfiltered view searches every route currently in the
dataset. Sweden-wide species discovery (including sparse species such as
harfågel or järv) requires the Phase 4 data platform described in the roadmap.

## Try the pilot

The application is a no-build static site. Serve the repository over HTTP (ES
modules and `fetch` do not work reliably from a `file://` URL):

```bash
python3 -m http.server 8000
```

Then open <http://localhost:8000>. The interface supports English, Swedish, and
Polish; trail-first and species-first search; optional county/municipality
filters; map selection; interactive Red List classes; and day, 30-day, 90-day,
365-day, or custom date ranges within the ten-year snapshot. Counts are computed
from daily aggregates and therefore change with every selected date range.

### Refresh the public data snapshot

Install the geospatial dependencies in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Provide the SOS subscription key through the environment and run the refresh:

```bash
SOS_SUBSCRIPTION_KEY_FILE=/absolute/path/to/specieskey.txt \
  .venv/bin/python scripts/refresh_data.py --days 3650
```

`SOS_SUBSCRIPTION_KEY` can be used instead of a file. Neither form is written to
`data/catalog.json`; `.env*`, `secrets/`, and raw responses are ignored by Git.
The full job discovers named route relations in Halmstad through Overpass,
reconstructs their geometry, creates a 200-metre buffer in SWEREF 99 TM, and
pages through public SOS results. Date windows are recursively split before the
SOS 10,000-result pagination edge, then deduplicated by occurrence ID. The job
emits a geometry catalog, a daily aggregate search index, and compact route/time
partitions. Use `--incremental` to replace only the current-month partitions and
rebuild the aggregates; the scheduled workflow runs this mode every 24 hours.

Run the test suite with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

### PostGIS data platform

The accepted Sweden-wide target is PostgreSQL/PostGIS, not a browser-sized copy
per route. A canonical observation is stored once and linked spatially to any
number of trails and, later, nature reserves. Daily `taxon × feature × date`
aggregates serve responsive period counts and rankings. Provider records remain
separate so SOS/Artportalen and GBIF provenance can be retained and cross-source
duplicates can later resolve to one canonical observation.

For a local development database, copy `.env.example` to the ignored `.env`, set
a local password, and run:

```bash
docker compose up -d database
.venv/bin/python scripts/migrate_postgis.py
.venv/bin/python scripts/import_postgis.py
.venv/bin/python scripts/verify_postgis.py
```

The database port is bound to `127.0.0.1` only. A self-hosted deployment exposes
an HTTPS API through a reverse proxy or outbound tunnel; PostgreSQL port 5432 is
never exposed publicly. See
[the PostGIS data-platform decision](docs/architecture/postgis-data-platform.md)
for schema, multilingual taxonomy, daily sync, reserve support, local-hosting,
and migration details.

## Product principles

1. **A route is the unit of discovery.** Points and species become useful when
   they help someone choose or understand a walk.
2. **Show the evidence.** Rankings expose counts, dates, sources, and coverage
   instead of presenting an unexplained score.
3. **Do not expose sensitive wildlife.** VildaLeder uses only locations made
   public by the source and never attempts to reverse obfuscation or reveal
   protected nests, dens, or sites.
4. **Conservation status is not a popularity score.** Red List category reflects
   extinction risk, not legal protection, beauty, or guaranteed rarity at a
   specific location.
5. **Design for imperfect data.** OSM routes may be fragmented or unnamed;
   observation density is affected by reporting effort, season, accessibility,
   and coordinate uncertainty.
6. **Keep source adapters replaceable.** Artportalen/SOS and GBIF have different
   identifiers, limits, update cycles, and taxonomies. Product logic must not be
   coupled to one response format.
7. **Earn complexity.** Authentication, payments, and native apps come after a
   useful anonymous web experience.

## Proposed system shape

```mermaid
flowchart LR
    OSM[OpenStreetMap routes] --> DB[(PostgreSQL / PostGIS)]
    Admin[Counties and municipalities] --> DB
    Reserves[Nature reserves] --> DB
    SOS[SLU SOS / Artportalen] --> DB
    GBIF[GBIF / Darwin Core] --> DB
    Taxa[Dyntaxa / Red List] --> DB
    DB --> Daily[Daily feature × taxon aggregates]
    Daily --> Export[Static pilot exports]
    Daily --> API[HTTPS API]
    DB --> API
    Export --> Web[GitHub Pages pilot]
    API --> Clients[Web and future mobile clients]
```

The initial GitHub Pages application is static, but not every data operation can
run safely in a browser. The SLU developer portal issues an API key after product
subscription; that key must not be embedded in frontend JavaScript. The pilot
therefore uses a static JavaScript client and a Python data job which generates a
versioned, cacheable JSON snapshot. A scheduled GitHub Actions job refreshes the
public snapshot with a repository secret. The accepted national path is the
implemented PostGIS schema plus an HTTPS API; static exports remain available
during that transition.

GitHub Pages is suitable for an open prototype, but it is not the intended
hosting platform for a future commercial SaaS or subscription backend.

## Implemented pilot stack

- Web: standards-based HTML, CSS, and JavaScript modules with no build step.
- Map: MapLibre GL JS 5.11 with an OSM raster source, resilient resize handling,
  GPU-rendered trail/corridor layers, and clickable Red List observation points.
- Localisation: checked-in UI dictionaries for `en`, `sv`, and `pl`.
- Spatial processing: Python, Shapely, and pyproj; corridors are calculated in
  SWEREF 99 TM (`EPSG:3006`) and exported as WGS84 GeoJSON.
- Data: route relations from OSM/Overpass and public Artportalen observations
  from SLU SOS, stored as a compact geometry catalog, daily aggregate index, and
  lazily loaded route/time partitions.
- Scale target: PostgreSQL 18/PostGIS 3.6 with canonical observations,
  source-record provenance, native spatial matching, multilingual taxon names,
  and daily trail/reserve aggregates.
- Automation: Python contract tests, CI, a daily SOS/OSM snapshot refresh using a
  GitHub Actions secret, and a GitHub Pages workflow.

This deliberately low-complexity frontend proves the two core journeys.
TypeScript, vector/PMTiles route delivery, a dedicated tile provider, and a
PostGIS-backed service remain scale-up options rather than prerequisites for
learning from the Halmstad pilot.

No basemap provider is selected yet. The public OpenStreetMap tile service is
best-effort and has a specific usage policy; a production or offline mobile app
must use a provider whose terms and capacity match the product.

## Data and ranking contract

### Trail identity

Each trail should retain its OSM relation ID, source version/timestamp, geometry,
name, reference, network level, operator, and available multilingual names. A
route without a stable name can be displayed with a deterministic fallback but
must not silently merge with another route.

### Spatial matching

- Build a metric 200-metre buffer around the complete route geometry.
- Query observations by the buffer polygon, not only by a route bounding box.
- Retain coordinate uncertainty and source precision where supplied.
- Deduplicate records using stable source identifiers before counting.
- Record the trail version, query time, observation source, filters, and data
  version so a result can be reproduced.

### Time ranges

- Day: rolling 24 hours by default.
- Month: rolling 30 days.
- Quarter: rolling 90 days.
- Year: rolling 365 days.
- Custom: explicit inclusive start and end dates.

The UI must display the interpreted dates and timezone rather than relying only
on labels such as “month”.

### Red List ordering

The default conservation ordering will follow the Swedish categories:

`RE → CR → EN → VU → NT → DD → LC → NE/NA`

Current observations of a taxon classified as regionally extinct (`RE`) require
special presentation because they may represent historical data, a taxonomic
change, or a record needing validation. Category, assessment year, and source
must be shown. Users will also be able to switch to recency, observation count,
or alphabetical sorting.

The pilot currently displays the category returned in
`taxon.attributes.redlistCategory` by SOS. The search response does not provide
the assessment year, so the UI does not claim a Red List edition. Explicit 2025
assessment provenance remains a pre-expansion data-contract task.

### Species-to-trail ranking

The first transparent baseline is the number of deduplicated observations inside
the trail corridor and selected period. Raw counts are biased by trail length and
reporting effort, so the results should also expose:

- observations per kilometre;
- number of distinct observation dates;
- most recent observation;
- coordinate precision and validation status when available;
- corridor area and trail length;
- data-source coverage.

A composite “best trail” score will not be introduced until the baseline can be
evaluated against real examples.

## Multilingual search

The UI and all product-owned content will ship in English, Swedish, and Polish.
Taxon search will support:

- accepted scientific names;
- scientific synonyms;
- recommended Swedish names from Dyntaxa;
- available English and Polish names from authoritative sources;
- a curated alias layer where authoritative coverage is incomplete;
- accent-insensitive autocomplete with the scientific name always visible.

The canonical identity is a source taxon identifier, never the displayed name.

## Data safety and limitations

- Only public observations are in scope for anonymous users.
- Obfuscated or withheld source coordinates stay obfuscated or withheld.
- Observation locations must not be interpreted as trail safety guidance or
  permission to enter private/restricted land.
- Trail presence in OSM does not guarantee that it is open, maintained, safe, or
  legally accessible at the selected time.
- Seasonal closures, fire restrictions, hunting, weather, accessibility, and
  transport are valuable future layers but are outside the first MVP.
- All source attribution and downstream licence obligations must be visible and
  auditable before launch.
- The snapshot is evidence of reported observations, not a probability model or
  a promise that a species will be present.
- The standard OpenStreetMap tile service is used only at pilot scale; it is not
  the selected production or offline basemap.
- Pilot common-name autocomplete is Swedish plus scientific names. English and
  Polish taxon-name sources are not yet integrated, although the complete UI is
  translated.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for milestones, acceptance criteria, risks, and the
path from data discovery to web MVP and native mobile applications.

## Authoritative references

- [OpenStreetMap hiking route tagging](https://wiki.openstreetmap.org/wiki/Tag:route%3Dhiking)
- [OpenStreetMap copyright and attribution](https://www.openstreetmap.org/copyright)
- [Overpass API documentation and public-instance policy](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
- [SLU overview of open data and APIs](https://www.slu.se/artdatabanken/rapportering-och-fynd/oppna-data-och-apier/om-slu-artdatabankens-apier)
- [SLU Species Observation System API capabilities](https://www.slu.se/artdatabanken/rapportering-och-fynd/oppna-data-och-apier/om-slu-artdatabankens-apier/api-for-artobservationer-fran-flera-dataset/)
- [Species Observation System technical repository](https://github.com/biodiversitydata-se/SOS)
- [Dyntaxa taxonomy API overview](https://www.slu.se/artdatabanken/rapportering-och-fynd/oppna-data-och-apier/om-slu-artdatabankens-apier/apier-for-taxonomisk-information/)
- [Swedish Red List 2025](https://www.slu.se/artdatabanken/publikationer/rodlistor/rodlista-2025/)
- [GBIF Occurrence API](https://techdocs.gbif.org/en/openapi/v1/occurrence)
- [SCB digital county and municipality boundaries](https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/digitala-granser/)
- [MapLibre GL JS](https://maplibre.org/maplibre-gl-js/docs/)
- [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)

## Licence

Project code is licensed under the [Apache License 2.0](LICENSE). It permits
open use, modification, distribution, and commercial use while retaining
copyright, licence, and notice obligations. This code licence does not replace
the separate attribution and downstream obligations of OSM, Artportalen/SOS, or
other source datasets.
