CREATE TABLE IF NOT EXISTS import_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','ok','failed')),
  fetched INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS offers (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  group_id TEXT,
  url TEXT,
  address TEXT,
  district TEXT,
  lat REAL, lon REAL,
  rooms INTEGER,
  area_total REAL,
  floor INTEGER, floors_total INTEGER,
  price INTEGER,
  deposit_raw TEXT, commission_raw TEXT, prepayment_raw TEXT,
  utilities_raw TEXT, pets_raw TEXT, rent_period_raw TEXT,
  description TEXT,
  photos_json TEXT NOT NULL DEFAULT '[]',
  published_at TEXT, last_seen TEXT,
  raw_json TEXT NOT NULL,
  import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
  PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS offers_group ON offers(group_id);

CREATE TABLE IF NOT EXISTS photo_features (
  photo_url TEXT NOT NULL,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  model TEXT NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (photo_url, model),
  FOREIGN KEY (source, source_id) REFERENCES offers(source, source_id)
);

CREATE TABLE IF NOT EXISTS search_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  monthly_max INTEGER,
  move_in_max INTEGER,
  rooms_json TEXT NOT NULL DEFAULT '[]',
  districts_json TEXT NOT NULL DEFAULT '[]',
  pets_required INTEGER NOT NULL DEFAULT 0,
  sample_photo_url TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('save','skip')),
  created_at TEXT NOT NULL,
  undone INTEGER NOT NULL DEFAULT 0
);
