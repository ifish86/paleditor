-- paleditor schema.
--
-- Two ownership rules drive the whole design:
--   * ingest-owned tables are rewritten on every pass, keyed by rev
--   * app-owned tables (chest_meta, pending_edits) are never touched by ingest
--
-- The container GUID is the join key throughout.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ingests (
    rev          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    save_mtime   REAL,
    save_sha256  TEXT,
    chest_count  INTEGER,
    -- 'running' | 'ok' | 'failed'. A failed ingest keeps its error so the UI
    -- can show stale data with a warning instead of guessing.
    status       TEXT    NOT NULL DEFAULT 'running',
    error        TEXT,
    backend      TEXT,
    duration_ms  INTEGER
);

CREATE TABLE IF NOT EXISTS bases (
    base_guid     TEXT PRIMARY KEY,
    name          TEXT,
    x             REAL,
    y             REAL,
    z             REAL,
    guild_id      TEXT,
    last_seen_rev INTEGER NOT NULL REFERENCES ingests(rev)
);

CREATE TABLE IF NOT EXISTS chests (
    container_guid TEXT PRIMARY KEY,
    base_guid      TEXT REFERENCES bases(base_guid),
    object_type    TEXT,
    x              REAL,
    y              REAL,
    z              REAL,
    -- Nullable on purpose. The lock code lives on the map object's concrete
    -- model data and is the field most likely to move between game versions,
    -- so every layer treats it as optional and degrades to contents-only.
    lock_code      TEXT,
    -- The container's capacity, not the number of occupied slots. The save
    -- stores only occupied slots, so these differ for any chest with room left.
    slot_count     INTEGER NOT NULL DEFAULT 0,
    guild_id       TEXT,
    -- 'storage'   player-built chests, what the app browses and edits
    -- 'loot'      world treasure boxes, which respawn constantly
    -- 'station'   feed boxes, furnaces and the like
    -- 'lock-only' doors that carry a code but hold nothing
    kind           TEXT NOT NULL DEFAULT 'storage',
    -- Whether the object has a lock module at all. Being lockable with no code
    -- set is a different state from not being lockable.
    lockable       INTEGER NOT NULL DEFAULT 0,
    has_container  INTEGER NOT NULL DEFAULT 1,
    last_seen_rev  INTEGER NOT NULL REFERENCES ingests(rev)
);

CREATE INDEX IF NOT EXISTS chests_base_idx ON chests(base_guid, kind);
CREATE INDEX IF NOT EXISTS chests_kind_idx ON chests(kind);
CREATE INDEX IF NOT EXISTS chests_rev_idx  ON chests(last_seen_rev);

CREATE TABLE IF NOT EXISTS slots (
    container_guid TEXT    NOT NULL REFERENCES chests(container_guid) ON DELETE CASCADE,
    slot_index     INTEGER NOT NULL,
    item_id        TEXT,
    stack_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (container_guid, slot_index)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS slots_item_idx ON slots(item_id);

-- Hand-curated catalogue, seeded from live dumps. This is a curation job, not
-- a parse job: an item_id absent here renders as its raw string rather than
-- being hidden.
CREATE TABLE IF NOT EXISTS items (
    item_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    category     TEXT,
    max_stack    INTEGER,
    -- 'seed'     : shipped with the app
    -- 'confirmed': read back from a live container dump
    -- 'observed' : seen in a save but not yet named by hand
    provenance   TEXT NOT NULL DEFAULT 'seed',
    -- Filename of a downloaded icon, relative to the icon directory, or NULL.
    -- Icons are fetched by 'paleditor fetch-icons' and never committed: they
    -- are the game publisher's artwork. An item without one falls back to its
    -- category colour, which every item has.
    icon         TEXT
);

-- App-owned. Survives every reparse; this is what makes nicknames stable.
CREATE TABLE IF NOT EXISTS chest_meta (
    container_guid TEXT PRIMARY KEY,
    nickname       TEXT,
    notes          TEXT,
    updated_at     TEXT NOT NULL
);

-- App-owned. Never derived from the save.
CREATE TABLE IF NOT EXISTS pending_edits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    container_guid TEXT    NOT NULL,
    slot_index     INTEGER NOT NULL,
    -- NULL item_id with stack_count 0 means "clear this slot".
    item_id        TEXT,
    stack_count    INTEGER NOT NULL DEFAULT 0,
    requested_by   TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','applying','applied','failed','cancelled')),
    requested_at   TEXT    NOT NULL,
    applied_at     TEXT,
    error          TEXT,
    batch_id       TEXT
);

CREATE INDEX IF NOT EXISTS pending_edits_status_idx ON pending_edits(status);
CREATE INDEX IF NOT EXISTS pending_edits_chest_idx  ON pending_edits(container_guid, status);

-- At most one outstanding edit per slot, so a queue cannot contain two
-- conflicting writes to the same place. Partial index: applied, failed and
-- cancelled rows stay for history without blocking a new edit.
CREATE UNIQUE INDEX IF NOT EXISTS pending_edits_open_slot_idx
    ON pending_edits(container_guid, slot_index)
    WHERE status IN ('queued','applying');

-- Single-row table holding values the app owns outside any ingest, such as
-- the generated session signing key.
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
