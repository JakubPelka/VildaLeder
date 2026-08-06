BEGIN;

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'vildaleder.spatial_feature'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%feature_kind%'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE vildaleder.spatial_feature DROP CONSTRAINT %I',
            constraint_name
        );
    END IF;
END
$$;

ALTER TYPE spatial_feature_kind RENAME TO spatial_feature_kind_v2;

CREATE TYPE spatial_feature_kind AS ENUM (
    'trail',
    'reserve',
    'national_park',
    'bird_hide',
    'observation_tower',
    'observation_site',
    'urban_green'
);

ALTER TABLE vildaleder.spatial_feature
ALTER COLUMN feature_kind TYPE spatial_feature_kind
USING feature_kind::text::spatial_feature_kind;

DROP TYPE spatial_feature_kind_v2;

ALTER TABLE vildaleder.spatial_feature
ADD CONSTRAINT spatial_feature_kind_geometry_check CHECK (
    (feature_kind = 'trail' AND GeometryType(geom) IN ('LINESTRING', 'MULTILINESTRING'))
    OR (
        feature_kind IN ('reserve', 'national_park', 'urban_green')
        AND GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
    )
    OR (
        feature_kind IN ('bird_hide', 'observation_tower', 'observation_site', 'urban_green')
        AND GeometryType(geom) IN ('POINT', 'MULTIPOINT')
    )
);

INSERT INTO vildaleder.metadata(key, value) VALUES ('schema_version', '3')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

INSERT INTO vildaleder.schema_migration(version, name)
VALUES (3, '003_urban_green.sql');

COMMIT;
