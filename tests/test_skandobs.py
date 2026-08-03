import unittest
import json
from datetime import date
from pathlib import Path

from scripts.sync_skandobs import (
    DEFAULT_DAYS,
    KNOWN_TAXA,
    build_snapshot,
    public_record,
    search_payload,
    year_windows,
)
from scripts.import_skandobs import spatial_feature_identity


class SkandobsAdapterTests(unittest.TestCase):
    def test_postgis_feature_identity_supports_nvv_destinations(self):
        self.assertEqual(
            spatial_feature_identity("nvl-led-30380768"),
            ("nvl", "led-30380768"),
        )
        self.assertEqual(
            spatial_feature_identity("nvl-site-30500401"),
            ("nvl", "site-30500401"),
        )

    def test_search_is_anonymous_halland_and_date_bounded(self):
        payload = search_payload(date(2025, 8, 4), date(2026, 8, 3))
        criteria = payload["searchCriteria"]["searchCriteria"][0]
        self.assertEqual(criteria["country"], "1")
        self.assertEqual(criteria["county"], "13")
        self.assertEqual(criteria["fromDate"], "2025-08-04")
        self.assertEqual(criteria["toDate"], "2026-08-03")
        self.assertEqual(DEFAULT_DAYS, 3_650)

    def test_year_windows_are_non_overlapping_and_inclusive(self):
        self.assertEqual(
            list(year_windows(date(2025, 12, 30), date(2026, 1, 2))),
            [
                (date(2025, 12, 30), date(2025, 12, 31)),
                (date(2026, 1, 1), date(2026, 1, 2)),
            ],
        )

    def test_public_record_whitelists_fields_and_drops_personal_data(self):
        source = {
            "observationID": "11111111-1111-1111-1111-111111111111",
            "publicID": 123,
            "speciesID": 100057,
            "species": "Lodjur",
            "count": 1,
            "date": "31.07.2026",
            "validationID": 5,
            "validationStatus": "Korrekt art",
            "activity": "Synobservation",
            "municipalityName": "Hylte",
            "latitude": 56.9,
            "longitude": 13.1,
            "diffused": False,
            "hidden": False,
            "protect": False,
            "user": "must not be copied",
            "userTelephone": "must not be copied",
            "comment": "must not be copied",
            "validatorName": "must not be copied",
        }
        result = public_record(source)
        self.assertEqual(result["taxonId"], "skandobs:100057")
        self.assertEqual(result["date"], "2026-07-31")
        for field in ("user", "userTelephone", "comment", "validatorName"):
            self.assertNotIn(field, result)

    def test_hidden_or_protected_records_are_rejected(self):
        self.assertIsNone(public_record({"hidden": True}))
        self.assertIsNone(public_record({"protect": True}))

    def test_snapshot_matches_points_to_feature_analysis_geometry(self):
        features = {
            "meta": {"bufferMeters": 200},
            "features": [
                {
                    "id": "nvr-1",
                    "analysisGeometry": {
                        "type": "Polygon",
                        "coordinates": [[[12, 56], [13, 56], [13, 57], [12, 57], [12, 56]]],
                    },
                }
            ],
        }
        detail = {
            "observationID": "11111111-1111-1111-1111-111111111111",
            "publicID": 123,
            "speciesID": 100057,
            "species": "Lodjur",
            "count": 1,
            "date": "31.07.2026",
            "validationID": 5,
            "validationStatus": "Korrekt art",
            "activity": "Synobservation",
            "municipalityName": "Hylte",
            "latitude": 56.5,
            "longitude": 12.5,
            "hidden": False,
            "protect": False,
        }
        snapshot = build_snapshot(
            features,
            [{"observationID": detail["observationID"]}],
            [detail],
            date(2025, 8, 4),
            date(2026, 8, 3),
        )
        self.assertEqual(snapshot["matches"], {"nvr-1": [detail["observationID"]]})
        self.assertEqual(snapshot["trails"]["nvr-1"], [["2026-07-31", 1]])
        self.assertEqual(snapshot["taxa"][0]["redlistCategory"], "VU")
        serialized = str(snapshot)
        self.assertNotIn("must not be copied", serialized)

    def test_known_predators_have_multilingual_names_and_2025_categories(self):
        self.assertEqual(set(KNOWN_TAXA), {100145, 100024, 100057, 100066})
        self.assertEqual(KNOWN_TAXA[100024]["redlistCategory"], "EN")
        for taxon in KNOWN_TAXA.values():
            self.assertEqual(set(taxon["vernacularNames"]), {"sv", "en", "pl"})


class GeneratedSkandobsSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "skandobs.json").read_text(
                encoding="utf-8"
            )
        )

    def test_snapshot_is_complete_private_field_free_and_ten_year_bounded(self):
        meta = self.snapshot["meta"]
        records = self.snapshot["records"]
        feature_catalog = json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "features.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(meta["windowStart"], "2016-08-06")
        self.assertEqual(meta["windowEnd"], "2026-08-03")
        self.assertGreaterEqual(meta["publicObservationsInArea"], 600)
        self.assertGreaterEqual(len(records), 50)
        self.assertEqual(len(records), meta["matchedObservations"])
        self.assertEqual(len({record["id"] for record in records}), len(records))
        self.assertGreaterEqual(sum(map(len, self.snapshot["matches"].values())), 70)
        self.assertTrue(
            set(self.snapshot["matches"]).issubset(
                {feature["id"] for feature in feature_catalog["features"]}
            )
        )
        serialized = json.dumps(self.snapshot)
        for forbidden in (
            "userTelephone",
            "userName",
            "validatorName",
            "validationComment",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
