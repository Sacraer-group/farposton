-- Proposed additive migration for farposton commit a697ee5d.
-- Apply after the existing flats/schema.sql, with foreign_keys enabled.
-- No ALTER, DROP, triggers, changes to offers/group_id, or writes to legacy rows.
-- SQL is repeatable. Application code validates JSON, UTC timestamps and addresses.

CREATE TABLE IF NOT EXISTS houses (
  id INTEGER PRIMARY KEY,
  city TEXT NOT NULL,
  street_type TEXT NOT NULL,
  street_name TEXT NOT NULL,
  house_number TEXT NOT NULL,
  corpus TEXT NOT NULL DEFAULT '',
  structure TEXT NOT NULL DEFAULT '',
  address_display TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (city, street_type, street_name, house_number, corpus, structure)
);

CREATE TABLE IF NOT EXISTS offer_houses (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  house_id INTEGER REFERENCES houses(id),
  offer_address_raw TEXT,
  match_status TEXT NOT NULL
    CHECK (match_status IN ('exact','manual','unmatched','ambiguous','stale')),
  match_basis TEXT NOT NULL,
  evidence_url TEXT,
  checked_at TEXT NOT NULL,
  note TEXT,
  candidates_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (source, source_id),
  FOREIGN KEY (source, source_id) REFERENCES offers(source, source_id),
  CHECK (
    (match_status IN ('exact','manual') AND house_id IS NOT NULL) OR
    (match_status IN ('unmatched','ambiguous','stale') AND house_id IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS offer_houses_house ON offer_houses(house_id);

-- One immutable observation/attempt for one house and logical source section.
-- source_key stays stable when a document URL or edition changes.
-- Latest attempt and latest successful observation are separate read concepts.
CREATE TABLE IF NOT EXISTS house_sources (
  id INTEGER PRIMARY KEY,
  house_id INTEGER NOT NULL REFERENCES houses(id),
  source_key TEXT NOT NULL,
  provider TEXT NOT NULL,
  section TEXT NOT NULL CHECK (section IN
    ('identity','facts','contacts','coordinates','outages','repairs')),
  source_url TEXT NOT NULL,
  source_type TEXT NOT NULL,
  external_house_id TEXT,
  matched_address TEXT,
  match_status TEXT NOT NULL CHECK (match_status IN
    ('exact','manual','unmatched','ambiguous')),
  checked_at TEXT NOT NULL,
  fetched_at TEXT,
  recorded_at TEXT NOT NULL,
  published_at TEXT,
  source_as_of TEXT,
  document_date TEXT,
  fetch_status TEXT NOT NULL CHECK (fetch_status IN
    ('ok','empty','not_found','blocked','error','parse_error','ambiguous')),
  error TEXT,
  artifact_path TEXT,
  content_sha256 TEXT,
  coverage_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (house_id, source_key, checked_at)
);
CREATE INDEX IF NOT EXISTS house_sources_recent
  ON house_sources(house_id, source_key, checked_at DESC);

-- Fixed columns, one source observation per row: no universal attribute/value store.
-- A multi-source input block is split by source URL and observation time.
CREATE TABLE IF NOT EXISTS house_facts (
  source_id INTEGER PRIMARY KEY REFERENCES house_sources(id),
  postal_code TEXT,
  construction_year INTEGER,
  commissioning_year INTEGER,
  floors_text TEXT,
  entrances INTEGER,
  wall_material TEXT,
  building_area_text TEXT,
  premises_area_text TEXT,
  central_heating_text TEXT,
  hot_water_text TEXT,
  cold_water_text TEXT,
  lat REAL,
  lon REAL,
  coordinates_precision TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  CHECK ((lat IS NULL AND lon IS NULL) OR
         (lat IS NOT NULL AND lon IS NOT NULL AND
          lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180))
);

-- The organization is a house-source assertion, not a globally resolved company.
-- City-directory numbers may be displayed as general contacts, never house providers.
CREATE TABLE IF NOT EXISTS house_contacts (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES house_sources(id),
  contact_key TEXT NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN
    ('management','emergency','lift','maintenance','resource_provider','city_service')),
  scope TEXT NOT NULL CHECK (scope IN ('house','city_directory')),
  phone_raw TEXT,
  dispatch_phone_raw TEXT,
  email TEXT,
  office_address TEXT,
  website TEXT,
  inn TEXT,
  ogrn TEXT,
  relationship_source_url TEXT,
  contract_start_reported TEXT,
  relationship_status TEXT NOT NULL CHECK (relationship_status IN
    ('published_claim','primary_confirmed','unresolved_conflict','not_house_specific')),
  conflict_key TEXT,
  conflict_note TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (source_id, contact_key),
  CHECK ((scope = 'house' AND relationship_source_url IS NOT NULL AND
          relationship_status <> 'not_house_specific') OR
         (scope = 'city_directory' AND relationship_status = 'not_house_specific')),
  CHECK ((relationship_status = 'unresolved_conflict' AND conflict_key IS NOT NULL) OR
         (relationship_status <> 'unresolved_conflict' AND conflict_key IS NULL))
);

-- Each row is an event assertion for the house on its parent source observation.
-- One VL blackout can affect many houses; per-house duration is not global duration.
CREATE TABLE IF NOT EXISTS house_events (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES house_sources(id),
  event_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('outage','repair_plan','repair_execution')),
  source_event_id TEXT,
  title TEXT NOT NULL,
  resource TEXT,
  organization_name TEXT,
  status_reported TEXT,
  period_text TEXT,
  starts_at TEXT,
  ends_at TEXT,
  year_from INTEGER,
  year_to INTEGER,
  completion_reported INTEGER CHECK (completion_reported IN (0,1)),
  source_url TEXT NOT NULL,
  source_locator TEXT,
  published_at TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (source_id, event_key),
  CHECK (year_from IS NULL OR year_to IS NULL OR year_from <= year_to),
  CHECK (kind = 'repair_execution' OR completion_reported IS NULL)
);
