# Skandobs source evaluation

Checked: 2026-08-03

## Finding

Skandobs is now enabled as an experimental, best-effort source for the open
VildaLeder pilot. It is not treated as a dependable commercial source.

The current web client calls an ASP.NET Web API at
`https://www.skandobs.no/skandobsAPI/`. Its published help index exposes an
anonymous observation-search operation:

`POST Area/API_Observations_Select/{searchID}/{userID}/{langID}/{showAll}/{page}`

An anonymous, read-only test for Hallands län (`county=13`) and the period
2025-08-03 through 2026-08-03 returned 189 public reports. List responses can
include a public observation identifier, species, observation date and time,
activity, validation status, municipality, and coordinates. The current web
form exposes bear, lynx, wolverine, and wolf; SLU's service description also
describes Skandobs reporting for golden eagle, so species coverage must be
confirmed rather than inferred from one client version.

The first complete Halland run over the maximum ten-year VildaLeder window
examined 672 public reports. Seventy-four public wolf/lynx observations fell
inside at least one trail corridor or buffered reserve, producing 90 feature
matches across 55 places. The checked snapshot was generated on 2026-08-03.

## Adapter policy

- Requests are anonymous, bounded by Halland and non-overlapping date windows,
  and recursively split if the map-result cap is reached.
- Lightweight map results are spatially filtered first; full details are
  requested only for candidate observations near a feature.
- The detail endpoint exposes reporter/contact data and validator identities.
  VildaLeder uses an explicit public whitelist and stores none of those fields,
  comments, or validation comments.
- Only the public coordinates supplied by Skandobs are used. Generalisation is
  retained, and VildaLeder never combines data to reconstruct hidden precision.
- Validation status remains attached to the observation and visible in its map
  popup; a report is not silently presented as a confirmed inventory result.
- Refresh output is atomic. API failure keeps the previous good snapshot, and
  the daily workflow continues refreshing the independent sources.

## Remaining limitations

- The help page contains machine-oriented endpoint details but no general API
  contract, versioning policy, quota, availability promise, or support status
  for third-party integrations.
- No clear licence or redistribution/caching terms for observation records were
  found in the public developer surface.
- Skandobs reports can contain sensitive large-predator evidence. VildaLeder
  must use only the public coordinates supplied by Skandobs and must not combine
  sources in a way that reconstructs a hidden or more precise location.
- Validation state must remain visible; a public report is not automatically a
  confirmed inventory result.
- Cross-source deduplication with Artportalen/SOS or Rovbase needs stable source
  identifiers and conservative rules.

## Decision and follow-up

Use the adapter for open-MVP learning, accepting that it may break. Before
depending on it for a commercial or availability-sensitive product, ask the
service owner to confirm in writing:

1. whether this endpoint is supported for automated third-party read access;
2. rate limits and the preferred incremental-sync method;
3. licence, attribution, caching, retention, correction, and deletion duties;
4. which species and validation states are public;
5. how hidden/generalised observations are represented;
6. whether public Rovbase records are a preferred integration source.

SLU lists `support.skandobs@slu.se` as the Skandobs support contact. No email has
been sent as part of this evaluation.

## Authoritative references

- [Skandobs](https://www.skandobs.se/)
- [Skandobs Web API help index](https://www.skandobs.no/skandobsAPI/help)
- [SLU: databases for wildlife management](https://www.slu.se/om-slu/organisation/institutioner/ekologi/slu-viltskadecenter/stod-i-viltforvaltningen/databaser-for-forvaltningen/)
- [SLU: report large predators](https://www.slu.se/centrumbildningar-och-projekt/viltskadecenter/Inventering/inventering-av-stora-rovdjur/har-du-sett-rovdjur/)
