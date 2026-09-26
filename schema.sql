CREATE SCHEMA IF NOT EXISTS grid;

BEGIN;

-- ============================================================
-- Prices
--
-- Source:
--   Delivery Date
--   Delivery Hour
--   Delivery Interval
--   Repeated Hour Flag
--   Settlement Point Name
--   Settlement Point Type
--   Settlement Point Price
-- ============================================================

CREATE TABLE grid.prices (
    id                      BIGSERIAL PRIMARY KEY,
    ts                      TIMESTAMPTZ NOT NULL,
    settlement_point        TEXT NOT NULL,
    settlement_point_type   TEXT,
    price                   DOUBLE PRECISION,
    repeated_hour_flag      BOOLEAN,
    source_month            DATE NOT NULL
);

CREATE INDEX prices_ts_idx
    ON grid.prices (ts);

CREATE INDEX prices_point_ts_idx
    ON grid.prices (settlement_point, ts);


-- ============================================================
-- Demand
--
-- CSV is wide:
--
-- Hour Ending, COAST, EAST, ..., WEST, ERCOT
--
-- Loader converts it to:
--
-- ts, region, demand_mw
-- ============================================================

CREATE TABLE grid.demand (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    region          TEXT NOT NULL,
    demand_mw       DOUBLE PRECISION,
    source_month    DATE NOT NULL
);

CREATE INDEX demand_ts_idx
    ON grid.demand (ts);

CREATE INDEX demand_region_ts_idx
    ON grid.demand (region, ts);


-- ============================================================
-- Fuel mix
--
-- CSV is wide:
--
-- Date,Fuel,Settlement Type,Total,0:15,0:30,...,23:45,0:00
--
-- Loader converts each 15-minute value to one row.
-- ============================================================

CREATE TABLE grid.fuel_mix (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL,
    fuel_type           TEXT NOT NULL,
    settlement_type     TEXT,
    generation_mw       DOUBLE PRECISION,
    source_month        DATE NOT NULL
);

CREATE INDEX fuel_mix_ts_idx
    ON grid.fuel_mix (ts);

CREATE INDEX fuel_mix_fuel_ts_idx
    ON grid.fuel_mix (fuel_type, ts);


-- ============================================================
-- Capacity
--
-- Keep the fields needed for analysis as real columns.
-- Preserve the complete source row in raw.
-- ============================================================

CREATE TABLE grid.capacity (
    id                          BIGSERIAL PRIMARY KEY,

    publication_ts              TIMESTAMPTZ,
    interval_ts                 TIMESTAMPTZ NOT NULL,

    available_mw                DOUBLE PRECISION,
    available_reserve_mw        DOUBLE PRECISION,

    cap_gen_res_total           DOUBLE PRECISION,
    cap_load_res_total          DOUBLE PRECISION,
    offline_available_mw_total  DOUBLE PRECISION,

    repeated_hour_flag          BOOLEAN,

    source_month                DATE NOT NULL,

    raw                         JSONB NOT NULL
);

CREATE INDEX capacity_interval_idx
    ON grid.capacity (interval_ts);

CREATE INDEX capacity_publication_idx
    ON grid.capacity (publication_ts);

CREATE INDEX capacity_interval_publication_idx
    ON grid.capacity (
        interval_ts,
        publication_ts DESC
    );


-- ============================================================
-- Outages
--
-- Preserve the full original row in raw.
-- ============================================================

CREATE TABLE grid.outages (
    id                          BIGSERIAL PRIMARY KEY,

    publication_ts              TIMESTAMPTZ,
    operating_date              DATE,

    resource_name               TEXT,
    resource_unit_code          TEXT,
    resource_type               TEXT,
    outage_type                 TEXT,

    outage_mw                   DOUBLE PRECISION,

    available_mw_max            DOUBLE PRECISION,
    available_mw_during_outage  DOUBLE PRECISION,

    start_ts                    TIMESTAMPTZ,
    planned_end_ts              TIMESTAMPTZ,
    end_ts                      TIMESTAMPTZ,

    nature_of_work              TEXT,

    source_month                DATE NOT NULL,

    raw                         JSONB NOT NULL
);

CREATE INDEX outages_publication_idx
    ON grid.outages (publication_ts);

CREATE INDEX outages_operating_date_idx
    ON grid.outages (operating_date);

CREATE INDEX outages_start_idx
    ON grid.outages (start_ts);

CREATE INDEX outages_resource_idx
    ON grid.outages (resource_name);

COMMIT;
