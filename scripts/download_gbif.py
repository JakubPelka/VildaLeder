import argparse
import csv
import io
import os
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone

import psycopg
import requests


GBIF_USER = os.getenv("GBIF_USER")
GBIF_PASSWORD = os.getenv("GBIF_PASSWORD")
GBIF_EMAIL = os.getenv("GBIF_EMAIL")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def iso_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

def request_gbif_download(gadm_gid: str, target_year: int) -> str:
    payload = {
        "creator": GBIF_USER,
        "notificationAddresses": [GBIF_EMAIL],
        "format": "SIMPLE_CSV",
        "predicate": {
            "type": "and",
            "predicates": [
                {"type": "equals", "key": "GADM_GID", "value": gadm_gid} if gadm_gid != "SWE" else {"type": "equals", "key": "COUNTRY", "value": "SE"},
                {"type": "equals", "key": "YEAR", "value": str(target_year)}
            ]
        }
    }
    print("Requesting GBIF download...", file=sys.stderr)
    response = requests.post(
        "https://api.gbif.org/v1/occurrence/download/request",
        json=payload,
        auth=(GBIF_USER, GBIF_PASSWORD)
    )
    if response.status_code != 201:
        raise RuntimeError(f"Error starting download: {response.status_code} {response.text}")

    download_key = response.text.strip()
    print(f"Download started! Key: {download_key}", file=sys.stderr)

    while True:
        status_resp = requests.get(f"https://api.gbif.org/v1/occurrence/download/{download_key}")
        status_resp.raise_for_status()
        status_data = status_resp.json()
        status = status_data.get("status")
        print(f"Status: {status}", file=sys.stderr)
        
        if status == "SUCCEEDED":
            return status_data.get("downloadLink")
        elif status in ("KILLED", "FAILED", "CANCELLED"):
            raise RuntimeError(f"Download failed with status: {status}")
        
        time.sleep(15)

def get_gbif_archive(gadm_gid: str, target_year: int, force_download: bool) -> str:
    archive_dir = os.path.join(ROOT_DIR, "data", "gbif-archives", gadm_gid)
    os.makedirs(archive_dir, exist_ok=True)
    archive_path = os.path.join(archive_dir, f"{target_year}.zip")

    if os.path.exists(archive_path) and not force_download:
        print(f"Using local archive for {target_year}: {archive_path}", file=sys.stderr)
        return archive_path

    download_link = request_gbif_download(gadm_gid, target_year)
    
    print(f"Downloading ZIP from {download_link} to {archive_path}...", file=sys.stderr)
    r = requests.get(download_link, stream=True)
    r.raise_for_status()

    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        for chunk in r.iter_content(chunk_size=8192):
            tmp_file.write(chunk)
        tmp_file.flush()
        os.rename(tmp_file.name, archive_path)

    return archive_path

def load_data(archive_path: str, db_url: str):
    print("Extracting and loading to PostgreSQL...", file=sys.stderr)
    with zipfile.ZipFile(archive_path, 'r') as z:
            csv_filename = [name for name in z.namelist() if name.endswith('.csv')][0]
            with z.open(csv_filename) as f:
                wrapper = io.TextIOWrapper(f, encoding='utf-8')
                reader = csv.DictReader(wrapper, delimiter='\t')
                
                with psycopg.connect(db_url) as conn:
                    # Create TEMP table
                    conn.execute("""
                        CREATE TEMP TABLE gbif_import (
                            gbif_id text,
                            species_key text,
                            decimal_latitude float,
                            decimal_longitude float,
                            coordinate_uncertainty float,
                            event_date text,
                            individual_count int,
                            issue text,
                            scientific_name text
                        )
                    """)
                    
                    with conn.cursor() as cur:
                        with cur.copy("COPY gbif_import (gbif_id, species_key, decimal_latitude, decimal_longitude, coordinate_uncertainty, event_date, individual_count, issue, scientific_name) FROM STDIN") as copy:
                            count = 0
                            for row in reader:
                                if not row.get("decimalLatitude") or not row.get("decimalLongitude"):
                                    continue
                                taxon = row.get("speciesKey") or row.get("taxonKey")
                                if not taxon:
                                    continue
                                
                                ind_count = row.get("individualCount")
                                try:
                                    ind_count = int(ind_count) if ind_count else None
                                except ValueError:
                                    ind_count = None

                                copy.write_row((
                                    row.get("gbifID"),
                                    taxon,
                                    float(row["decimalLatitude"]),
                                    float(row["decimalLongitude"]),
                                    float(row["coordinateUncertaintyInMeters"]) if row.get("coordinateUncertaintyInMeters") else None,
                                    row.get("eventDate"),
                                    ind_count,
                                    row.get("issue"),
                                    row.get("scientificName")
                                ))
                                count += 1
                                if count % 100000 == 0:
                                    print(f"Loaded {count} rows...", file=sys.stderr)
                    
                    print(f"Total rows loaded to temp table: {count}", file=sys.stderr)
                    
                    conn.execute("INSERT INTO vildaleder.data_source (source_key, name, source_kind) VALUES ('gbif', 'GBIF', 'observation') ON CONFLICT (source_key) DO NOTHING")
                    gbif_source_id = conn.execute("SELECT source_id FROM vildaleder.data_source WHERE source_key = 'gbif'").fetchone()[0]
                    
                    print("Mapping Taxa...", file=sys.stderr)
                    conn.execute(f"""
                        -- Find existing taxa by scientific name
                        WITH distinct_taxa AS (
                            SELECT DISTINCT species_key, scientific_name FROM gbif_import
                        ),
                        matched_taxa AS (
                            SELECT dt.species_key, dt.scientific_name, t.taxon_id
                            FROM distinct_taxa dt
                            LEFT JOIN vildaleder.taxon t ON t.scientific_name = dt.scientific_name
                        ),
                        inserted_taxa AS (
                            INSERT INTO vildaleder.taxon(canonical_source_id, canonical_source_taxon_id, scientific_name)
                            SELECT {gbif_source_id}, species_key, scientific_name
                            FROM matched_taxa WHERE taxon_id IS NULL
                            ON CONFLICT DO NOTHING
                            RETURNING taxon_id, canonical_source_taxon_id as species_key
                        )
                        INSERT INTO vildaleder.taxon_external_id (taxon_id, source_id, external_id)
                        SELECT taxon_id, {gbif_source_id}, species_key FROM matched_taxa WHERE taxon_id IS NOT NULL
                        UNION
                        SELECT taxon_id, {gbif_source_id}, species_key FROM inserted_taxa
                        ON CONFLICT DO NOTHING
                    """)

                    print("Spatial Join and Insert Observations...", file=sys.stderr)
                    conn.execute(f"""
                        CREATE TEMP TABLE gbif_filtered AS
                        SELECT DISTINCT ON (g.gbif_id)
                            g.gbif_id,
                            ext.taxon_id,
                            g.event_date,
                            g.individual_count,
                            g.decimal_latitude,
                            g.decimal_longitude,
                            g.coordinate_uncertainty,
                            false as uncertain_identification
                        FROM gbif_import g
                        JOIN vildaleder.taxon_external_id ext ON ext.source_id = {gbif_source_id} AND ext.external_id = g.species_key
                        JOIN vildaleder.spatial_feature f ON ST_Intersects(ST_SetSRID(ST_MakePoint(g.decimal_longitude, g.decimal_latitude), 4326), f.geom)
                    """)

                    print("Inserting into vildaleder.observation...", file=sys.stderr)
                    conn.execute(f"""
                        INSERT INTO vildaleder.observation(
                            canonical_key, taxon_id, observed_on, individual_count, verified,
                            uncertain_identification, geom, coordinate_uncertainty_m, first_seen_at, last_seen_at
                        )
                        SELECT 
                            'gbif:' || gbif_id,
                            taxon_id,
                            SUBSTRING(event_date FROM 1 FOR 10)::date,
                            individual_count,
                            true as verified,
                            uncertain_identification,
                            ST_SetSRID(ST_MakePoint(decimal_longitude, decimal_latitude), 4326),
                            coordinate_uncertainty,
                            %s::timestamptz,
                            %s::timestamptz
                        FROM gbif_filtered
                        ON CONFLICT (canonical_key) DO UPDATE
                        SET taxon_id = EXCLUDED.taxon_id,
                            observed_on = EXCLUDED.observed_on,
                            individual_count = EXCLUDED.individual_count,
                            geom = EXCLUDED.geom,
                            is_deleted = false,
                            last_seen_at = EXCLUDED.last_seen_at
                    """, (iso_timestamp(), iso_timestamp()))

                    print("Inserting source records...", file=sys.stderr)
                    conn.execute(f"""
                        INSERT INTO vildaleder.observation_source_record(
                            observation_id, source_id, source_record_id, is_deleted,
                            first_seen_at, last_seen_at
                        )
                        SELECT 
                            o.observation_id,
                            {gbif_source_id},
                            'gbif-' || g.gbif_id,
                            false,
                            %s::timestamptz,
                            %s::timestamptz
                        FROM gbif_filtered g
                        JOIN vildaleder.observation o ON o.canonical_key = 'gbif:' || g.gbif_id
                        ON CONFLICT (source_id, source_record_id) DO UPDATE
                            SET is_deleted = false,
                            last_seen_at = EXCLUDED.last_seen_at
                    """, (iso_timestamp(), iso_timestamp()))

                    conn.commit()

def main():
    parser = argparse.ArgumentParser(description="Bulk download GBIF occurrences")
    parser.add_argument("--gadm", type=str, default="SWE.13_1", help="GADM region ID (default Halland SWE.13_1)")
    parser.add_argument("--years", type=int, default=10, help="Number of years to go back")
    parser.add_argument("--force-download", action="store_true", help="Force redownload even if local archive exists")
    args = parser.parse_args()

    if not all([GBIF_USER, GBIF_PASSWORD, GBIF_EMAIL]):
        print("Error: Missing GBIF credentials in .env", file=sys.stderr)
        sys.exit(1)

    current_year = datetime.now().year
    start_year = current_year - args.years
    database_url = os.environ.get("DATABASE_URL", "postgresql://vildaleder:vildaleder@127.0.0.1:5432/vildaleder")

    for year in range(start_year, current_year + 1):
        print(f"\n--- Fetching GBIF data for {args.gadm} in year {year} ---", file=sys.stderr)
        archive_path = get_gbif_archive(args.gadm, year, args.force_download)
        print(f"Ready for {year}! Local archive: {archive_path}", file=sys.stderr)
        load_data(archive_path, database_url)
        print(f"Done with {year}!", file=sys.stderr)
        
    print("All years completed successfully!", file=sys.stderr)

if __name__ == "__main__":
    main()
