#!/usr/bin/env python3

import argparse
import csv
import json
import os
import re
import sys

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg


# ============================================================
# Configuration
# ============================================================

CENTRAL = ZoneInfo("America/Chicago")
UTC = timezone.utc

# Explicit ERCOT meanings.
CDT = timezone(timedelta(hours=-5))
CST = timezone(timedelta(hours=-6))

FILES = (
    "prices.csv",
    "demand.csv",
    "fuel_mix.csv",
    "capacity.csv",
    "outages.csv",
)

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
TIME_COL_RE = re.compile(r"^\d{1,2}:\d{2}$")

csv.field_size_limit(sys.maxsize)


# ============================================================
# Basic conversion
# ============================================================

def clean(value):
    if value is None:
        return None

    value = str(value).strip()

    return value or None


def number(value):
    value = clean(value)

    if value is None:
        return None

    value = value.replace(",", "").replace("$", "")

    try:
        return float(value)
    except ValueError:
        return None


def integer(value):
    value = number(value)

    if value is None:
        return None

    return int(value)


def boolean(value):
    value = clean(value)

    if value is None:
        return None

    value = value.lower()

    if value in ("1", "y", "yes", "true", "t"):
        return True

    if value in ("0", "n", "no", "false", "f"):
        return False

    return None


def month_date(month):
    return date.fromisoformat(month + "-01")


# ============================================================
# Date parsing
# ============================================================

def parse_date(value):
    value = clean(value)

    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value
        ).date()
    except ValueError:
        pass

    for fmt in (
            "%m/%d/%Y",
            "%Y-%m-%d",
            "%m/%d/%y",
    ):
        try:
            return datetime.strptime(
                value,
                fmt,
            ).date()
        except ValueError:
            pass
        
        return None

def old_parse_date(value):
    value = clean(value)

    if not value:
        return None

    for fmt in (
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(
                value,
                fmt,
            ).date()

        except ValueError:
            pass

    return None


def local_to_utc(
    naive,
    marker=None,
    repeated=False,
):
    """
    Convert ERCOT local wall-clock time to UTC.

    Explicit markers:
        DST / CDT -> UTC-5
        STD / CST -> UTC-6

    Otherwise use America/Chicago.

    repeated=True selects the second occurrence of
    an ambiguous fall-back hour.
    """

    marker = (
        marker.upper()
        if marker
        else None
    )

    if marker in ("DST", "CDT"):
        aware = naive.replace(
            tzinfo=CDT
        )

    elif marker in ("STD", "CST"):
        aware = naive.replace(
            tzinfo=CST
        )

    else:
        aware = naive.replace(
            tzinfo=CENTRAL,
            fold=1 if repeated else 0,
        )

    return aware.astimezone(UTC)


# ============================================================
# Full ERCOT timestamp
# ============================================================

FULL_TS_RE = re.compile(
    r"""
    ^
    (\d{1,2}/\d{1,2}/\d{4}
     |
     \d{4}-\d{2}-\d{2})
    \s+
    (\d{1,2})
    :
    (\d{2})
    (?:
        :
        (\d{2})
    )?
    (?:
        \s+
        (DST|STD|CDT|CST)
    )?
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)


def parse_ercot_ts(
    value,
    repeated=False,
):
    """
    Examples:

        08/01/2026 01:00
        08/01/2026 24:00
        11/03/2024 01:00 DST
        11/03/2024 01:00 STD

    Returns UTC-aware datetime.
    """

    value = clean(value)

    if not value:
        return None

    match = FULL_TS_RE.match(value)

    if not match:
        return None

    d = parse_date(
        match.group(1)
    )

    if d is None:
        return None

    hour = int(match.group(2))
    minute = int(match.group(3))
    second = int(match.group(4) or 0)
    marker = match.group(5)

    if hour == 24:
        if minute != 0 or second != 0:
            return None

        d += timedelta(days=1)
        hour = 0

    if hour > 23:
        return None

    naive = datetime.combine(
        d,
        time(
            hour,
            minute,
            second,
        ),
    )

    return local_to_utc(
        naive,
        marker=marker,
        repeated=repeated,
    )


# ============================================================
# Publication timestamps
# ============================================================

def publication_ts(row):
    d = clean(
        row.get("_publication_date")
    )

    t = clean(
        row.get("_publication_time")
    )

    if not d:
        return None

    if not t:
        t = "00:00"

    return parse_ercot_ts(
        f"{d} {t}"
    )


# ============================================================
# Prices
# ============================================================

def price_ts(row):
    d = parse_date(
        row.get("Delivery Date")
    )

    hour = integer(
        row.get("Delivery Hour")
    )

    interval = integer(
        row.get("Delivery Interval")
    )

    if (
        d is None
        or hour is None
        or interval is None
    ):
        return None

    if not 1 <= hour <= 24:
        return None

    if not 1 <= interval <= 4:
        return None

    naive = (
        datetime.combine(
            d,
            time.min,
        )
        +
        timedelta(
            hours=hour - 1,
            minutes=interval * 15,
        )
    )

    repeated = boolean(
        row.get(
            "Repeated Hour Flag"
        )
    ) is True

    return local_to_utc(
        naive,
        repeated=repeated,
    )


def load_prices(conn, path, month):
    sql = """
        COPY grid.prices (
            ts,
            settlement_point,
            settlement_point_type,
            price,
            repeated_hour_flag,
            source_month
        )
        FROM STDIN
    """

    count = 0
    skipped = 0

    with path.open(
        newline="",
        encoding="utf-8-sig",
        errors="replace",
    ) as f:

        reader = csv.DictReader(f)

        with conn.cursor() as cur:
            with cur.copy(sql) as copy:

                for row in reader:

                    ts = price_ts(row)

                    point = clean(
                        row.get(
                            "Settlement Point Name"
                        )
                    )

                    if ts is None or point is None:
                        skipped += 1
                        continue

                    copy.write_row((
                        ts,
                        point,

                        clean(
                            row.get(
                                "Settlement Point Type"
                            )
                        ),

                        number(
                            row.get(
                                "Settlement Point Price"
                            )
                        ),

                        boolean(
                            row.get(
                                "Repeated Hour Flag"
                            )
                        ),

                        month_date(month),
                    ))

                    count += 1

    return count, skipped


# ============================================================
# Demand
# ============================================================

def load_demand(conn, path, month):
    sql = """
        COPY grid.demand (
            ts,
            region,
            demand_mw,
            source_month
        )
        FROM STDIN
    """

    count = 0
    skipped = 0

    with path.open(
        newline="",
        encoding="utf-8-sig",
        errors="replace",
    ) as f:

        reader = csv.DictReader(f)

        regions = [
            field
            for field in reader.fieldnames
            if field != "Hour Ending"
        ]

        with conn.cursor() as cur:
            with cur.copy(sql) as copy:

                for row in reader:

                    ts = parse_ercot_ts(
                        row.get(
                            "Hour Ending"
                        )
                    )

                    if ts is None:
                        skipped += 1
                        continue

                    for region in regions:

                        mw = number(
                            row.get(region)
                        )

                        if mw is None:
                            continue

                        copy.write_row((
                            ts,
                            region,
                            mw,
                            month_date(month),
                        ))

                        count += 1

    return count, skipped


# ============================================================
# Fuel mix
# ============================================================

def fuel_ts(day_value, column):
    d = parse_date(day_value)

    if d is None:
        return None

    if not TIME_COL_RE.match(column):
        return None

    hour, minute = map(
        int,
        column.split(":"),
    )

    # Final 0:00 column means midnight
    # at the end of the operating day.
    if hour == 0 and minute == 0:
        d += timedelta(days=1)

    naive = datetime.combine(
        d,
        time(hour, minute),
    )

    return local_to_utc(naive)


def load_fuel_mix(conn, path, month):
    sql = """
        COPY grid.fuel_mix (
            ts,
            fuel_type,
            settlement_type,
            generation_mw,
            source_month
        )
        FROM STDIN
    """

    count = 0
    skipped = 0

    with path.open(
        newline="",
        encoding="utf-8-sig",
        errors="replace",
    ) as f:

        reader = csv.DictReader(f)

        intervals = [
            field
            for field in reader.fieldnames
            if TIME_COL_RE.match(field or "")
        ]

        with conn.cursor() as cur:
            with cur.copy(sql) as copy:

                for row in reader:

                    fuel = clean(
                        row.get("Fuel")
                    )

                    if not fuel:
                        skipped += 1
                        continue

                    for interval in intervals:

                        mw = number(
                            row.get(interval)
                        )

                        if mw is None:
                            continue

                        ts = fuel_ts(
                            row.get("Date"),
                            interval,
                        )

                        if ts is None:
                            continue

                        copy.write_row((
                            ts,
                            fuel,

                            clean(
                                row.get(
                                    "Settlement Type"
                                )
                            ),

                            mw,
                            month_date(month),
                        ))

                        count += 1

    return count, skipped


# ============================================================
# Capacity
# ============================================================

def capacity_ts(row):
    d = parse_date(
        row.get("DeliveryDate")
    )

    value = clean(
        row.get("HourEnding")
    )

    if d is None or value is None:
        return None

    marker = None

    parts = value.split()

    if (
        parts
        and
        parts[-1].upper()
        in ("DST", "STD", "CDT", "CST")
    ):
        marker = parts[-1].upper()
        value = " ".join(parts[:-1])

    try:
        hour = int(float(value))

        naive = (
            datetime.combine(
                d,
                time.min,
            )
            +
            timedelta(hours=hour)
        )

    except ValueError:

        match = re.match(
            r"^(\d{1,2}):(\d{2})$",
            value,
        )

        if not match:
            return None

        hour = int(match.group(1))
        minute = int(match.group(2))

        if hour == 24:
            d += timedelta(days=1)
            hour = 0

        naive = datetime.combine(
            d,
            time(hour, minute),
        )

    repeated = boolean(
        row.get(
            "RepeatedHourFlag"
        )
    ) is True

    return local_to_utc(
        naive,
        marker=marker,
        repeated=repeated,
    )


def load_capacity(conn, path, month):
    sql = """
        COPY grid.capacity (
            publication_ts,
            interval_ts,
            available_mw,
            available_reserve_mw,
            cap_gen_res_total,
            cap_load_res_total,
            offline_available_mw_total,
            repeated_hour_flag,
            source_month,
            raw
        )
        FROM STDIN
    """

    count = 0
    skipped = 0

    with path.open(
        newline="",
        encoding="utf-8-sig",
        errors="replace",
    ) as f:

        reader = csv.DictReader(f)

        with conn.cursor() as cur:
            with cur.copy(sql) as copy:

                for row in reader:

                    ts = capacity_ts(row)

                    if ts is None:
                        skipped += 1
                        continue

                    copy.write_row((
                        publication_ts(row),

                        ts,

                        number(
                            row.get(
                                "AvailCapGen"
                            )
                        ),

                        number(
                            row.get(
                                "AvailCapReserve"
                            )
                        ),

                        number(
                            row.get(
                                "CapGenResTotal"
                            )
                        ),

                        number(
                            row.get(
                                "CapLoadResTotal"
                            )
                        ),

                        number(
                            row.get(
                                "OfflineAvailableMWTotal"
                            )
                        ),

                        boolean(
                            row.get(
                                "RepeatedHourFlag"
                            )
                        ),

                        month_date(month),

                        json.dumps(row),
                    ))

                    count += 1

    return count, skipped


# ============================================================
# Outages
# ============================================================

def load_outages(conn, path, month):
    sql = """
        COPY grid.outages (
            publication_ts,
            operating_date,
            resource_name,
            resource_unit_code,
            resource_type,
            outage_type,
            outage_mw,
            available_mw_max,
            available_mw_during_outage,
            start_ts,
            planned_end_ts,
            end_ts,
            nature_of_work,
            source_month,
            raw
        )
        FROM STDIN
    """

    count = 0
    skipped = 0

    with path.open(
        newline="",
        encoding="utf-8-sig",
        errors="replace",
    ) as f:

        reader = csv.DictReader(f)

        with conn.cursor() as cur:
            with cur.copy(sql) as copy:

                for row in reader:

                    pub = publication_ts(row)

                    outage_mw = number(
                        row.get(
                            "Effective MW Reduction Due to Outage"
                        )
                    )

                    resource = clean(
                        row.get(
                            "Resource Name"
                        )
                    )

                    # Report junk/header/footer rows.
                    if (
                        outage_mw is None
                        and
                        resource is None
                    ):
                        skipped += 1
                        continue

                    copy.write_row((
                        pub,

                        (
                            pub.astimezone(
                                CENTRAL
                            ).date()
                            if pub
                            else None
                        ),

                        resource,

                        clean(
                            row.get(
                                "Resource Unit Code"
                            )
                        ),

                        clean(
                            row.get(
                                "Fuel Type"
                            )
                        ),

                        clean(
                            row.get(
                                "Outage Type"
                            )
                        ),

                        outage_mw,

                        number(
                            row.get(
                                "Available MW Maximum"
                            )
                        ),

                        number(
                            row.get(
                                "Available MW During Outage"
                            )
                        ),

                        parse_ercot_ts(
                            row.get(
                                "Actual Outage Start"
                            )
                        ),

                        parse_ercot_ts(
                            row.get(
                                "Planned End Date"
                            )
                        ),

                        parse_ercot_ts(
                            row.get(
                                "Actual End Date"
                            )
                        ),

                        clean(
                            row.get(
                                "Nature Of Work"
                            )
                        ),

                        month_date(month),

                        json.dumps(row),
                    ))

                    count += 1

    return count, skipped


# ============================================================
# Find complete months
# ============================================================

def complete_months(root):
    months = []

    for path in root.iterdir():

        if (
            not path.is_dir()
            or
            not MONTH_RE.match(path.name)
        ):
            continue

        if all(
            (path / filename).exists()
            for filename in FILES
        ):
            months.append(path.name)

    return sorted(months)


LOADERS = {
    "prices.csv": load_prices,
    "demand.csv": load_demand,
    "fuel_mix.csv": load_fuel_mix,
    "capacity.csv": load_capacity,
    "outages.csv": load_outages,
}


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        default="data/ercot",
    )

    parser.add_argument(
        "--db",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql:///postgres",
        ),
    )

    parser.add_argument(
        "--start",
    )

    parser.add_argument(
        "--end",
    )

    args = parser.parse_args()

    root = Path(args.root)

    months = complete_months(root)

    if args.start:
        months = [
            m for m in months
            if m >= args.start
        ]

    if args.end:
        months = [
            m for m in months
            if m <= args.end
        ]

    if not months:
        raise SystemExit(
            "No complete months found"
        )

    print(
        f"Loading {len(months)} complete months:"
    )

    print(
        f"  {months[0]} through {months[-1]}"
    )

    print()

    totals = {
        filename: 0
        for filename in FILES
    }

    with psycopg.connect(
        args.db
    ) as conn:

        # Everything inserted is already UTC.
        # Keep the loader session explicit too.
        conn.execute(
            "SET TIME ZONE 'UTC'"
        )

        for month in months:

            print(month)

            directory = (
                root / month
            )

            for filename in FILES:

                loader = LOADERS[
                    filename
                ]

                path = (
                    directory
                    /
                    filename
                )

                with conn.transaction():

                    count, skipped = loader(
                        conn,
                        path,
                        month,
                    )

                totals[
                    filename
                ] += count

                print(
                    f"  {filename:<16}"
                    f"{count:>12,} rows"
                    f"   skipped {skipped:,}"
                )

            print()

    print("Totals:")

    for filename in FILES:

        print(
            f"  {filename:<16}"
            f"{totals[filename]:>12,}"
        )

    print()
    print("Done.")


if __name__ == "__main__":
    main()
