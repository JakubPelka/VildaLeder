import re
import unittest
from pathlib import Path

from scripts.import_postgis import feature_identity, normalized_name
from scripts.migrate_postgis import migration_files


ROOT = Path(__file__).resolve().parents[1]


class PostgisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = (ROOT / "db" / "migrations" / "001_initial.sql").read_text(
            encoding="utf-8"
        )
        cls.compose = (ROOT / "compose.yml").read_text(encoding="utf-8")

    def test_target_is_postgis_with_private_local_binding(self):
        self.assertIn("postgis/postgis:18-3.6", self.compose)
        self.assertIn('127.0.0.1:${VILDA_DB_PORT:-5432}:5432', self.compose)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS postgis", self.schema)
        self.assertNotIn("sqlite", (ROOT / "README.md").read_text(encoding="utf-8").lower())

    def test_schema_normalizes_sources_taxa_names_and_observations(self):
        for table in (
            "data_source",
            "taxon_external_id",
            "taxon_name",
            "observation",
            "observation_source_record",
            "spatial_feature",
            "observation_feature",
            "daily_feature_taxon",
        ):
            self.assertIn(f"CREATE TABLE {table}", self.schema)
        self.assertIn("language_code text NOT NULL", self.schema)
        self.assertIn("taxon_name_kind", self.schema)
        self.assertNotIn("name_en", self.schema)
        self.assertNotIn("name_sv", self.schema)
        self.assertNotIn("name_pl", self.schema)

    def test_schema_has_native_spatial_indexes_and_incremental_refresh_functions(self):
        self.assertGreaterEqual(self.schema.count("USING gist"), 3)
        self.assertIn("geometry(Point, 4326)", self.schema)
        self.assertIn("ST_Intersects(feature.analysis_geom, observed.geom)", self.schema)
        self.assertIn("refresh_observation_feature_matches", self.schema)
        self.assertIn("refresh_daily_feature_taxon", self.schema)
        destination_migration = (
            ROOT / "db" / "migrations" / "002_destination_types.sql"
        ).read_text(encoding="utf-8")
        for feature_kind in (
            "trail",
            "reserve",
            "national_park",
            "bird_hide",
            "observation_tower",
            "observation_site",
        ):
            self.assertIn(f"'{feature_kind}'", destination_migration)

    def test_names_are_accent_insensitive_without_losing_stored_spelling(self):
        self.assertEqual(normalized_name("Havsörn"), "havsorn")
        self.assertEqual(normalized_name("Żubr europejski"), "zubr europejski")
        self.assertEqual(normalized_name("Järv"), "jarv")

    def test_snapshot_feature_identity_supports_spatial_sources(self):
        self.assertEqual(feature_identity({"osmRelationId": 8_394_095}), ("osm", "8394095"))
        self.assertEqual(
            feature_identity({"source": "nvr", "sourceFeatureId": "2001961"}),
            ("nvr", "2001961"),
        )
        self.assertEqual(
            feature_identity({"source": "nvl", "sourceFeatureId": "site-30500401"}),
            ("nvl", "site-30500401"),
        )

    def test_snapshot_feature_identity_rejects_missing_source_id(self):
        with self.assertRaisesRegex(ValueError, "has no source ID"):
            feature_identity({"id": "reserve-without-source"})

    def test_numbered_migration_is_discoverable(self):
        self.assertEqual(
            [(version, path.name) for version, path in migration_files()],
            [(1, "001_initial.sql"), (2, "002_destination_types.sql"), (3, "003_urban_green.sql")],
        )

    def test_refresh_windows_do_not_prune_canonical_observations_by_age(self):
        refresh_sources = "\n".join(
            (ROOT / "scripts" / name).read_text(encoding="utf-8")
            for name in (
                "sync_halland_postgis.py",
                "import_postgis.py",
                "import_skandobs.py",
                "server_refresh.sh",
            )
        )
        self.assertIsNone(
            re.search(
                r"DELETE\s+FROM\s+vildaleder\.observation(?:\s|$)",
                refresh_sources,
                flags=re.IGNORECASE,
            )
        )
        self.assertIn("refresh windows do not prune by age", refresh_sources)


if __name__ == "__main__":
    unittest.main()
