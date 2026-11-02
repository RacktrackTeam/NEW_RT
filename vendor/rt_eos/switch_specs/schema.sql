-- Switch specifications database schema
-- Single normalized table works fine for v1 (~500-1000 models)

CREATE TABLE IF NOT EXISTS switches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identification
    vendor TEXT NOT NULL,
    family TEXT,
    model TEXT NOT NULL,
    sku TEXT,

    -- Port configuration
    port_count INTEGER,
    port_speed_max_gbps INTEGER,
    port_config TEXT,         -- human-readable: "48x 1G + 4x SFP+"
    uplink_config TEXT,

    -- Performance
    switching_capacity_gbps REAL,
    forwarding_rate_mpps REAL,
    buffer_mb REAL,
    latency_ns INTEGER,

    -- Tables
    mac_table_size INTEGER,

    -- Power and PoE
    poe_standard TEXT,        -- PoE / PoE+ / PoE++ / UPOE+ / None
    poe_budget_w INTEGER,
    power_typical_w INTEGER,
    power_max_w INTEGER,

    -- Features
    layer TEXT,               -- L2 / L2+ / L3
    features TEXT,            -- JSON array

    -- Physical
    rack_units INTEGER,

    -- Software
    nos TEXT,                 -- IOS-XE / EOS / Junos / ArubaOS-CX / RouterOS / UniFi / Omada / Cumulus / SONiC

    -- Lifecycle
    status TEXT,              -- active / EoS / EoL

    -- Use case
    use_case TEXT,            -- access / aggregation / core / spine / leaf / ToR

    -- Source
    datasheet_url TEXT,
    image_url TEXT,           -- product image URL (linked, not stored bytes)
    extra_specs TEXT,         -- JSON dict of additional datasheet fields not in the schema
    last_updated TEXT
);

CREATE INDEX IF NOT EXISTS idx_vendor ON switches(vendor);
CREATE INDEX IF NOT EXISTS idx_model ON switches(model);
CREATE INDEX IF NOT EXISTS idx_sku ON switches(sku);
CREATE INDEX IF NOT EXISTS idx_family ON switches(family);
