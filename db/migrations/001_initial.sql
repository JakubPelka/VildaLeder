BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS vildaleder;
SET search_path TO vildaleder, public;

CREATE TYPE source_kind AS ENUM ('observation', 'taxonomy', 'spatial');
CREATE TYPE taxon_name_kind AS ENUM ('scientific', 'vernacular', 'synonym');
CREATE TYPE spatial_feature_kind AS ENUM ('trail', 'reserve');
CREATE TYPE sync_mode AS ENUM ('full', 'incremental', 'import');
CREATE TYPE sync_status AS ENUM ('running', 'complete', 'failed');

CREATE TABLE metadata (
    key text PRIMARY KEY,
    value text NOT NULL
);

CREATE TABLE schema_migration (
    version integer PRIMARY KEY,
    name text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE data_source (
    source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_key text NOT NULL UNIQUE,
    name text NOT NULL,
    source_kind source_kind NOT NULL,
    base_url text,
    licence text,
    attribution text,
    terms_checked_at date
);

CREATE TABLE taxon (
    taxon_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_source_id bigint REFERENCES data_source(source_id),
    canonical_source_taxon_id text,
    scientific_name text,
    taxon_rank text,
    taxonomic_status text,
    organism_group text,
    accepted_taxon_id bigint REFERENCES taxon(taxon_id),
    redlist_category text,
    redlist_assessment text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (canonical_source_id, canonical_source_taxon_id)
);

CREATE TABLE taxon_external_id (
    taxon_id bigint NOT NULL REFERENCES taxon(taxon_id) ON DELETE CASCADE,
    source_id bigint NOT NULL REFERENCES data_source(source_id),
    external_id text NOT NULL,
    is_accepted boolean NOT NULL DEFAULT true,
    PRIMARY KEY (source_id, external_id)
);

CREATE INDEX taxon_external_id_taxon_idx ON taxon_external_id(taxon_id);

CREATE TABLE taxon_name (
    taxon_name_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    taxon_id bigint NOT NULL REFERENCES taxon(taxon_id) ON DELETE CASCADE,
    language_code text NOT NULL CHECK (language_code ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'),
    name text NOT NULL,
    name_normalized text NOT NULL,
    name_kind taxon_name_kind NOT NULL,
    source_id bigint NOT NULL REFERENCES data_source(source_id),
    source_name_id text,
    is_preferred boolean NOT NULL DEFAULT false,
    valid_from date,
    valid_to date,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (taxon_id, language_code, name, name_kind, source_id)
);

CREATE INDEX taxon_name_taxon_idx ON taxon_name(taxon_id, is_preferred DESC);
CREATE INDEX taxon_name_language_idx ON taxon_name(language_code, is_preferred DESC);
CREATE INDEX taxon_name_autocomplete_idx
ON taxon_name USING gin (name_normalized gin_trgm_ops);

CREATE TABLE administrative_area (
    administrative_area_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id bigint NOT NULL REFERENCES data_source(source_id),
    source_area_id text NOT NULL,
    area_type text NOT NULL CHECK (area_type IN ('country', 'county', 'municipality')),
    code text,
    canonical_name text NOT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL,
    source_updated_at timestamptz,
    UNIQUE (source_id, source_area_id),
    CHECK (ST_IsValid(geom))
);

CREATE INDEX administrative_area_geom_gix ON administrative_area USING gist (geom);

CREATE TABLE spatial_feature (
    feature_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_kind spatial_feature_kind NOT NULL,
    source_id bigint NOT NULL REFERENCES data_source(source_id),
    source_feature_id text NOT NULL,
    canonical_name text NOT NULL,
    length_km double precision,
    geom geometry(Geometry, 4326) NOT NULL,
    analysis_geom geometry(Geometry, 4326) NOT NULL,
    source_url text,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    geometry_version text NOT NULL,
    source_updated_at timestamptz,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, source_feature_id),
    CHECK (ST_IsValid(geom)),
    CHECK (ST_IsValid(analysis_geom)),
    CHECK (
        (feature_kind = 'trail' AND GeometryType(geom) IN ('LINESTRING', 'MULTILINESTRING'))
        OR (feature_kind = 'reserve' AND GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON'))
    ),
    CHECK (GeometryType(analysis_geom) IN ('POLYGON', 'MULTIPOLYGON'))
);

CREATE INDEX spatial_feature_geom_gix ON spatial_feature USING gist (geom);
CREATE INDEX spatial_feature_analysis_geom_gix ON spatial_feature USING gist (analysis_geom);
CREATE INDEX spatial_feature_kind_active_idx ON spatial_feature(feature_kind, is_active);

CREATE TABLE feature_name (
    feature_id bigint NOT NULL REFERENCES spatial_feature(feature_id) ON DELETE CASCADE,
    language_code text NOT NULL CHECK (language_code ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'),
    name text NOT NULL,
    name_normalized text NOT NULL,
    source_id bigint NOT NULL REFERENCES data_source(source_id),
    is_preferred boolean NOT NULL DEFAULT false,
    PRIMARY KEY (feature_id, language_code, name, source_id)
);

CREATE INDEX feature_name_autocomplete_idx
ON feature_name USING gin (name_normalized gin_trgm_ops);

CREATE TABLE feature_administrative_area (
    feature_id bigint NOT NULL REFERENCES spatial_feature(feature_id) ON DELETE CASCADE,
    administrative_area_id bigint NOT NULL
        REFERENCES administrative_area(administrative_area_id) ON DELETE CASCADE,
    overlap_ratio double precision CHECK (overlap_ratio BETWEEN 0 AND 1),
    PRIMARY KEY (feature_id, administrative_area_id)
);

CREATE TABLE observation (
    observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_key text NOT NULL UNIQUE,
    taxon_id bigint REFERENCES taxon(taxon_id),
    observed_on date NOT NULL,
    observed_at timestamptz,
    individual_count double precision,
    verified boolean NOT NULL DEFAULT false,
    uncertain_identification boolean NOT NULL DEFAULT false,
    geom geometry(Point, 4326) NOT NULL,
    coordinate_uncertainty_m double precision,
    location_is_public boolean NOT NULL DEFAULT true,
    location_is_generalized boolean NOT NULL DEFAULT false,
    data_generalizations text,
    information_withheld text,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    is_deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ST_X(geom) BETWEEN -180 AND 180),
    CHECK (ST_Y(geom) BETWEEN -90 AND 90),
    CHECK (coordinate_uncertainty_m IS NULL OR coordinate_uncertainty_m >= 0)
);

CREATE INDEX observation_geom_gix ON observation USING gist (geom);
CREATE INDEX observation_taxon_date_idx ON observation(taxon_id, observed_on DESC)
WHERE NOT is_deleted AND location_is_public;
CREATE INDEX observation_date_brin ON observation USING brin (observed_on);

CREATE TABLE observation_source_record (
    observation_id bigint NOT NULL REFERENCES observation(observation_id) ON DELETE CASCADE,
    source_id bigint NOT NULL REFERENCES data_source(source_id),
    source_record_id text NOT NULL,
    source_url text,
    source_modified_at timestamptz,
    payload_hash text,
    is_primary boolean NOT NULL DEFAULT false,
    is_deleted boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    PRIMARY KEY (source_id, source_record_id),
    UNIQUE (observation_id, source_id, source_record_id)
);

CREATE INDEX observation_source_record_observation_idx
ON observation_source_record(observation_id);

CREATE TABLE observation_feature (
    observation_id bigint NOT NULL REFERENCES observation(observation_id) ON DELETE CASCADE,
    feature_id bigint NOT NULL REFERENCES spatial_feature(feature_id) ON DELETE CASCADE,
    match_method text NOT NULL DEFAULT 'within_analysis_geometry',
    distance_m double precision CHECK (distance_m IS NULL OR distance_m >= 0),
    feature_geometry_version text NOT NULL,
    matched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (observation_id, feature_id)
);

CREATE INDEX observation_feature_feature_idx
ON observation_feature(feature_id, observation_id);

CREATE TABLE daily_feature_taxon (
    feature_id bigint NOT NULL REFERENCES spatial_feature(feature_id) ON DELETE CASCADE,
    taxon_id bigint NOT NULL REFERENCES taxon(taxon_id) ON DELETE CASCADE,
    observed_on date NOT NULL,
    observation_count integer NOT NULL CHECK (observation_count >= 0),
    individual_count double precision,
    latest_observation_id bigint REFERENCES observation(observation_id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feature_id, taxon_id, observed_on)
);

CREATE INDEX daily_feature_taxon_species_ranking_idx
ON daily_feature_taxon(taxon_id, observed_on, observation_count DESC, feature_id);

CREATE TABLE sync_run (
    sync_run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id bigint REFERENCES data_source(source_id),
    mode sync_mode NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    window_start date,
    window_end date,
    status sync_status NOT NULL DEFAULT 'running',
    records_seen bigint NOT NULL DEFAULT 0,
    records_inserted bigint NOT NULL DEFAULT 0,
    records_updated bigint NOT NULL DEFAULT 0,
    error_message text
);

CREATE TABLE sync_cursor (
    source_id bigint PRIMARY KEY REFERENCES data_source(source_id),
    cursor_value text,
    last_complete_sync_at timestamptz,
    correction_window_start date
);

CREATE OR REPLACE FUNCTION refresh_observation_feature_matches(
    p_start date,
    p_end date
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    inserted_count bigint;
BEGIN
    DELETE FROM observation_feature matched
    USING observation observed
    WHERE matched.observation_id = observed.observation_id
      AND observed.observed_on BETWEEN p_start AND p_end;

    INSERT INTO observation_feature(
        observation_id,
        feature_id,
        match_method,
        feature_geometry_version,
        matched_at
    )
    SELECT
        observed.observation_id,
        feature.feature_id,
        'within_analysis_geometry',
        feature.geometry_version,
        now()
    FROM observation observed
    JOIN spatial_feature feature
      ON ST_Intersects(feature.analysis_geom, observed.geom)
    WHERE observed.observed_on BETWEEN p_start AND p_end
      AND NOT observed.is_deleted
      AND observed.location_is_public
      AND feature.is_active;

    GET DIAGNOSTICS inserted_count = ROW_COUNT;
    RETURN inserted_count;
END;
$$;

CREATE OR REPLACE FUNCTION refresh_daily_feature_taxon(
    p_start date,
    p_end date
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    inserted_count bigint;
BEGIN
    DELETE FROM daily_feature_taxon
    WHERE observed_on BETWEEN p_start AND p_end;

    INSERT INTO daily_feature_taxon(
        feature_id,
        taxon_id,
        observed_on,
        observation_count,
        individual_count,
        latest_observation_id,
        updated_at
    )
    SELECT
        matched.feature_id,
        observed.taxon_id,
        observed.observed_on,
        count(*)::integer,
        sum(observed.individual_count),
        (array_agg(
            observed.observation_id
            ORDER BY observed.observed_at DESC NULLS LAST, observed.observation_id DESC
        ))[1],
        now()
    FROM observation_feature matched
    JOIN observation observed USING (observation_id)
    WHERE observed.observed_on BETWEEN p_start AND p_end
      AND observed.taxon_id IS NOT NULL
      AND NOT observed.is_deleted
      AND observed.location_is_public
    GROUP BY matched.feature_id, observed.taxon_id, observed.observed_on;

    GET DIAGNOSTICS inserted_count = ROW_COUNT;
    RETURN inserted_count;
END;
$$;

INSERT INTO metadata(key, value) VALUES
    ('schema_version', '1'),
    ('spatial_reference', 'EPSG:4326');

INSERT INTO schema_migration(version, name)
VALUES (1, '001_initial.sql');

COMMIT;
