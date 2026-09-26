#!/usr/bin/env python3

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import psycopg


CENTRAL = ZoneInfo("America/Chicago")


# ============================================================
# Connection / time helpers
# ============================================================

def connect(db_url: str):
    conn = psycopg.connect(db_url)
    conn.execute("SET TIME ZONE 'America/Chicago'")
    return conn


def parse_local_dt(value: str | None):
    if value is None:
        return None

    value = value.strip()

    # YYYY-MM-DD
    if len(value) == 10:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=CENTRAL)

    # naive datetime
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CENTRAL)

    return dt


def latest_timestamp(conn, family: str):
    queries = {
        "demand": "select max(ts) from grid.demand",
        "prices": "select max(ts) from grid.prices",
        "fuel_mix": "select max(ts) from grid.fuel_mix",
        "capacity": "select max(interval_ts) from grid.capacity",
        "outages": "select max(publication_ts) from grid.outages",
        "reserve": """
            select greatest(
                (select max(ts) from grid.demand),
                (select max(interval_ts) from grid.capacity)
            )
        """,
    }

    with conn.cursor() as cur:
        cur.execute(queries[family])
        return cur.fetchone()[0]


@dataclass
class TimeRange:
    start: datetime
    end: datetime


def resolve_range(conn, family: str, start: str | None, end: str | None):
    start_dt = parse_local_dt(start)
    end_dt = parse_local_dt(end)

    if end_dt is None:
        latest = latest_timestamp(conn, family)
        if latest is None:
            raise RuntimeError(f"No data found for family {family}")
        latest_local = latest.astimezone(CENTRAL)
        end_date = latest_local.date() + timedelta(days=1)
        end_dt = datetime.combine(end_date, time.min, tzinfo=CENTRAL)

    if start_dt is None:
        start_dt = end_dt - timedelta(days=730)

    return TimeRange(start=start_dt, end=end_dt)


def maybe_save_show(fig, args):
    fig.tight_layout()

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=160)

    if not args.no_show:
        plt.show()

    plt.close(fig)


def read_sql_df(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


# ============================================================
# Demand
# ============================================================

def demand_line(conn, args):
    tr = resolve_range(conn, "demand", args.start, args.end)

    sql = f"""
        select
            date_trunc(%(bucket)s, ts) as t,
            avg(demand_mw) as demand_mw
        from grid.demand
        where region = %(region)s
          and ts >= %(start)s
          and ts < %(end)s
        group by 1
        order by 1
    """

    df = read_sql_df(conn, sql, {
        "bucket": args.bucket,
        "region": args.region,
        "start": tr.start,
        "end": tr.end,
    })

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df["t"], df["demand_mw"])
    ax.set_title(args.title or f"Demand ({args.region})")
    ax.set_ylabel("MW")
    ax.set_xlabel("Time")
    ax.grid(True, alpha=0.3)

    maybe_save_show(fig, args)


def demand_compare(conn, args):
    left_start = parse_local_dt(args.left_start)
    left_end = parse_local_dt(args.left_end)
    right_start = parse_local_dt(args.right_start)
    right_end = parse_local_dt(args.right_end)

    if None in (left_start, left_end, right_start, right_end):
        raise SystemExit("demand compare requires all four period arguments")

    sql = """
        select
            date_trunc('day', ts) as t,
            avg(demand_mw) as demand_mw
        from grid.demand
        where region = %(region)s
          and ts >= %(start)s
          and ts < %(end)s
        group by 1
        order by 1
    """

    left = read_sql_df(conn, sql, {
        "region": args.region,
        "start": left_start,
        "end": left_end,
    })
    right = read_sql_df(conn, sql, {
        "region": args.region,
        "start": right_start,
        "end": right_end,
    })

    left["idx"] = range(1, len(left) + 1)
    right["idx"] = range(1, len(right) + 1)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(left["idx"], left["demand_mw"], label=args.left_label or "Period A")
    ax.plot(right["idx"], right["demand_mw"], label=args.right_label or "Period B")
    ax.set_title(args.title or f"Demand comparison ({args.region})")
    ax.set_xlabel("Day in period")
    ax.set_ylabel("Average MW")
    ax.grid(True, alpha=0.3)
    ax.legend()

    maybe_save_show(fig, args)


def demand_monthly(conn, args):
    tr = resolve_range(conn, "demand", args.start, args.end)

    sql = """
        select
            date_trunc('month', ts) as t,
            avg(demand_mw) as avg_mw,
            max(demand_mw) as peak_mw
        from grid.demand
        where region = %(region)s
          and ts >= %(start)s
          and ts < %(end)s
        group by 1
        order by 1
    """

    df = read_sql_df(conn, sql, {
        "region": args.region,
        "start": tr.start,
        "end": tr.end,
    })

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df["t"], df["avg_mw"], label="Average MW")
    ax.plot(df["t"], df["peak_mw"], label="Peak MW")
    ax.set_title(args.title or f"Monthly demand trends ({args.region})")
    ax.set_ylabel("MW")
    ax.set_xlabel("Month")
    ax.grid(True, alpha=0.3)
    ax.legend()

    maybe_save_show(fig, args)


# ============================================================
# Prices
# ============================================================

def prices_daily(conn, args):
    tr = resolve_range(conn, "prices", args.start, args.end)

    sql = """
        select
            date_trunc('day', ts) as t,
            avg(price) as avg_price
        from grid.prices
        where ts >= %(start)s
          and ts < %(end)s
          and (%(point)s is null or settlement_point = %(point)s)
        group by 1
        order by 1
    """

    df = read_sql_df(conn, sql, {
        "start": tr.start,
        "end": tr.end,
        "point": args.point,
    })

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df["t"], df["avg_price"])
    ax.set_title(args.title or "Daily average prices")
    ax.set_ylabel("Price")
    ax.set_xlabel("Date")
    ax.grid(True, alpha=0.3)

    maybe_save_show(fig, args)


def prices_yoy(conn, args):
    tr = resolve_range(conn, "prices", args.start, args.end)
    prev_start = tr.start - timedelta(days=365)
    prev_end = tr.end - timedelta(days=365)

    sql = """
        select
            date_trunc('day', ts) as t,
            avg(price) as avg_price
        from grid.prices
        where ts >= %(start)s
          and ts < %(end)s
          and (%(point)s is null or settlement_point = %(point)s)
        group by 1
        order by 1
    """

    cur_df = read_sql_df(conn, sql, {
        "start": tr.start,
        "end": tr.end,
        "point": args.point,
    })
    prev_df = read_sql_df(conn, sql, {
        "start": prev_start,
        "end": prev_end,
        "point": args.point,
    })

    cur_df["idx"] = range(1, len(cur_df) + 1)
    prev_df["idx"] = range(1, len(prev_df) + 1)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(cur_df["idx"], cur_df["avg_price"], label="Current period")
    ax.plot(prev_df["idx"], prev_df["avg_price"], label="Prior year")
    ax.set_title(args.title or "Year-over-year price comparison")
    ax.set_xlabel("Day in period")
    ax.set_ylabel("Average price")
    ax.grid(True, alpha=0.3)
    ax.legend()

    maybe_save_show(fig, args)


def prices_spikes(conn, args):
    tr = resolve_range(conn, "prices", args.start, args.end)

    sql = """
        select
            ts,
            settlement_point,
            price
        from grid.prices
        where ts >= %(start)s
          and ts < %(end)s
          and (%(point)s is null or settlement_point = %(point)s)
        order by price desc nulls last
        limit %(top_n)s
    """

    df = read_sql_df(conn, sql, {
        "start": tr.start,
        "end": tr.end,
        "point": args.point,
        "top_n": args.top_n,
    })

    df = df.sort_values("price", ascending=True)

    fig, ax = plt.subplots(figsize=(12, 6))
    labels = df["ts"].dt.strftime("%Y-%m-%d %H:%M")
    ax.barh(labels, df["price"])
    ax.set_title(args.title or f"Top {args.top_n} price spikes")
    ax.set_xlabel("Price")
    ax.set_ylabel("Timestamp")

    maybe_save_show(fig, args)


# ============================================================
# Fuel mix
# ============================================================

def fuelmix_stacked(conn, args):
    tr = resolve_range(conn, "fuel_mix", args.start, args.end)

    sql = """
        select
            date_trunc(%(bucket)s, ts) as t,
            fuel_type,
            avg(generation_mw) as generation_mw
        from grid.fuel_mix
        where ts >= %(start)s
          and ts < %(end)s
          and (%(settlement_type)s is null or settlement_type = %(settlement_type)s)
        group by 1, 2
        order by 1, 2
    """

    df = read_sql_df(conn, sql, {
        "bucket": args.bucket,
        "start": tr.start,
        "end": tr.end,
        "settlement_type": args.settlement_type,
    })

    pivot = df.pivot(index="t", columns="fuel_type", values="generation_mw").fillna(0)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.stackplot(pivot.index, [pivot[c] for c in pivot.columns], labels=list(pivot.columns))
    ax.set_title(args.title or "Fuel mix (stacked)")
    ax.set_ylabel("MW")
    ax.set_xlabel("Time")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)

    maybe_save_show(fig, args)


def fuelmix_selected(conn, args):
    tr = resolve_range(conn, "fuel_mix", args.start, args.end)

    sql = """
        select
            date_trunc(%(bucket)s, ts) as t,
            fuel_type,
            avg(generation_mw) as generation_mw
        from grid.fuel_mix
        where ts >= %(start)s
          and ts < %(end)s
          and fuel_type = any(%(fuels)s)
          and (%(settlement_type)s is null or settlement_type = %(settlement_type)s)
        group by 1, 2
        order by 1, 2
    """

    df = read_sql_df(conn, sql, {
        "bucket": args.bucket,
        "start": tr.start,
        "end": tr.end,
        "fuels": args.fuels,
        "settlement_type": args.settlement_type,
    })

    fig, ax = plt.subplots(figsize=(12, 6))
    for fuel, sub in df.groupby("fuel_type"):
        ax.plot(sub["t"], sub["generation_mw"], label=fuel)

    ax.set_title(args.title or "Selected fuels")
    ax.set_ylabel("MW")
    ax.set_xlabel("Time")
    ax.legend()
    ax.grid(True, alpha=0.3)

    maybe_save_show(fig, args)


def fuelmix_yoy(conn, args):
    tr = resolve_range(conn, "fuel_mix", args.start, args.end)
    prev_start = tr.start - timedelta(days=365)
    prev_end = tr.end - timedelta(days=365)

    sql = """
        select
            date_trunc('day', ts) as t,
            fuel_type,
            avg(generation_mw) as generation_mw
        from grid.fuel_mix
        where ts >= %(start)s
          and ts < %(end)s
          and fuel_type = any(%(fuels)s)
          and (%(settlement_type)s is null or settlement_type = %(settlement_type)s)
        group by 1, 2
        order by 1, 2
    """

    cur_df = read_sql_df(conn, sql, {
        "start": tr.start,
        "end": tr.end,
        "fuels": args.fuels,
        "settlement_type": args.settlement_type,
    })
    prev_df = read_sql_df(conn, sql, {
        "start": prev_start,
        "end": prev_end,
        "fuels": args.fuels,
        "settlement_type": args.settlement_type,
    })

    fig, ax = plt.subplots(figsize=(12, 6))

    for fuel in args.fuels:
        cur_sub = cur_df[cur_df["fuel_type"] == fuel].copy()
        prev_sub = prev_df[prev_df["fuel_type"] == fuel].copy()
        cur_sub["idx"] = range(1, len(cur_sub) + 1)
        prev_sub["idx"] = range(1, len(prev_sub) + 1)

        ax.plot(cur_sub["idx"], cur_sub["generation_mw"], label=f"{fuel} current")
        ax.plot(prev_sub["idx"], prev_sub["generation_mw"], linestyle="--", label=f"{fuel} prior year")

    ax.set_title(args.title or "Fuel mix year-over-year comparison")
    ax.set_xlabel("Day in period")
    ax.set_ylabel("Average MW")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    maybe_save_show(fig, args)


# ============================================================
# Outages
# ============================================================

def outages_monthly(conn, args):
    tr = resolve_range(conn, "outages", args.start, args.end)

    sql = """
        with daily as (
            select
                date_trunc('day', publication_ts) as day,
                sum(outage_mw) as outage_mw
            from grid.outages
            where publication_ts >= %(start)s
              and publication_ts < %(end)s
            group by 1
        )
        select
            date_trunc('month', day) as t,
            avg(outage_mw) as avg_outage_mw
        from daily
        group by 1
        order by 1
    """

    df = read_sql_df(conn, sql, {
        "start": tr.start,
        "end": tr.end,
    })

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(df["t"].dt.strftime("%Y-%m"), df["avg_outage_mw"])
    ax.set_title(args.title or "Monthly outages")
    ax.set_ylabel("Average outage MW")
    ax.set_xlabel("Month")
    ax.tick_params(axis="x", rotation=45)

    maybe_save_show(fig, args)


def outages_seasonal(conn, args):
    left_start = parse_local_dt(args.left_start)
    left_end = parse_local_dt(args.left_end)
    right_start = parse_local_dt(args.right_start)
    right_end = parse_local_dt(args.right_end)

    if None in (left_start, left_end, right_start, right_end):
        raise SystemExit("outages seasonal requires all four period arguments")

    sql = """
        select
            date_trunc('day', publication_ts) as t,
            sum(outage_mw) as outage_mw
        from grid.outages
        where publication_ts >= %(start)s
          and publication_ts < %(end)s
        group by 1
        order by 1
    """

    left = read_sql_df(conn, sql, {
        "start": left_start,
        "end": left_end,
    })
    right = read_sql_df(conn, sql, {
        "start": right_start,
        "end": right_end,
    })

    left["idx"] = range(1, len(left) + 1)
    right["idx"] = range(1, len(right) + 1)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(left["idx"], left["outage_mw"], label=args.left_label or "Season A")
    ax.plot(right["idx"], right["outage_mw"], label=args.right_label or "Season B")
    ax.set_title(args.title or "Seasonal outage comparison")
    ax.set_xlabel("Day in period")
    ax.set_ylabel("Outage MW")
    ax.legend()
    ax.grid(True, alpha=0.3)

    maybe_save_show(fig, args)


def outages_worst(conn, args):
    tr = resolve_range(conn, "outages", args.start, args.end)

    sql = """
        select
            date_trunc('day', publication_ts) as t,
            sum(outage_mw) as outage_mw
        from grid.outages
        where publication_ts >= %(start)s
          and publication_ts < %(end)s
        group by 1
        order by outage_mw desc nulls last
        limit %(top_n)s
    """

    df = read_sql_df(conn, sql, {
        "start": tr.start,
        "end": tr.end,
        "top_n": args.top_n,
    })

    df = df.sort_values("outage_mw", ascending=True)

    fig, ax = plt.subplots(figsize=(12, 6))
    labels = df["t"].dt.strftime("%Y-%m-%d")
    ax.barh(labels, df["outage_mw"])
    ax.set_title(args.title or f"Worst {args.top_n} outage periods")
    ax.set_xlabel("Outage MW")
    ax.set_ylabel("Date")

    maybe_save_show(fig, args)


# ============================================================
# Capacity vs demand / reserve margin
# ============================================================

def reserve_plot(conn, args):
    tr = resolve_range(conn, "reserve", args.start, args.end)

    sql = """
        with latest_capacity as (
            select distinct on (interval_ts)
                interval_ts,
                available_mw
            from grid.capacity
            where interval_ts >= %(start)s
              and interval_ts < %(end)s
            order by interval_ts, publication_ts desc nulls last
        ),
        cap as (
            select
                date_trunc(%(bucket)s, interval_ts) as t,
                avg(available_mw) as available_mw
            from latest_capacity
            group by 1
        ),
        dem as (
            select
                date_trunc(%(bucket)s, ts) as t,
                avg(demand_mw) as demand_mw
            from grid.demand
            where region = %(region)s
              and ts >= %(start)s
              and ts < %(end)s
            group by 1
        )
        select
            dem.t,
            dem.demand_mw,
            cap.available_mw,
            (cap.available_mw - dem.demand_mw) / nullif(dem.demand_mw, 0) as reserve_margin
        from dem
        join cap using (t)
        order by dem.t
    """

    df = read_sql_df(conn, sql, {
        "bucket": args.bucket,
        "region": args.region,
        "start": tr.start,
        "end": tr.end,
    })

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df["t"], df["demand_mw"], label="Demand MW")
    ax.plot(df["t"], df["available_mw"], label="Available MW")
    ax.plot(df["t"], df["reserve_margin"] * 100.0, label="Reserve margin %")
    ax.set_title(args.title or "Capacity vs demand")
    ax.set_xlabel("Time")
    ax.set_ylabel("MW / %")
    ax.legend()
    ax.grid(True, alpha=0.3)

    maybe_save_show(fig, args)


# ============================================================
# CLI
# ============================================================

def add_common_plot_args(p):
    p.add_argument("--db", default="postgresql://ivie_user:ivie_secret@localhost:5432/ivie_db")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--output")
    p.add_argument("--no-show", action="store_true")
    p.add_argument("--title")


def build_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    # 1) Demand
    demand = sub.add_parser("demand")
    demand_sub = demand.add_subparsers(dest="mode", required=True)

    p = demand_sub.add_parser("line")
    add_common_plot_args(p)
    p.add_argument("--region", default="ERCOT")
    p.add_argument("--bucket", choices=["hour", "day", "week", "month"], default="day")
    p.set_defaults(func=demand_line, family="demand")

    p = demand_sub.add_parser("compare")
    add_common_plot_args(p)
    p.add_argument("--region", default="ERCOT")
    p.add_argument("--left-start", required=True)
    p.add_argument("--left-end", required=True)
    p.add_argument("--right-start", required=True)
    p.add_argument("--right-end", required=True)
    p.add_argument("--left-label")
    p.add_argument("--right-label")
    p.set_defaults(func=demand_compare, family="demand")

    p = demand_sub.add_parser("monthly")
    add_common_plot_args(p)
    p.add_argument("--region", default="ERCOT")
    p.set_defaults(func=demand_monthly, family="demand")

    # 2) Prices
    prices = sub.add_parser("prices")
    prices_sub = prices.add_subparsers(dest="mode", required=True)

    p = prices_sub.add_parser("daily")
    add_common_plot_args(p)
    p.add_argument("--point")
    p.set_defaults(func=prices_daily, family="prices")

    p = prices_sub.add_parser("yoy")
    add_common_plot_args(p)
    p.add_argument("--point")
    p.set_defaults(func=prices_yoy, family="prices")

    p = prices_sub.add_parser("spikes")
    add_common_plot_args(p)
    p.add_argument("--point")
    p.add_argument("--top-n", type=int, default=20)
    p.set_defaults(func=prices_spikes, family="prices")

    # 3) Fuel mix
    fuel = sub.add_parser("fuelmix")
    fuel_sub = fuel.add_subparsers(dest="mode", required=True)

    p = fuel_sub.add_parser("stacked")
    add_common_plot_args(p)
    p.add_argument("--settlement-type", default="INITIAL")
    p.add_argument("--bucket", choices=["hour", "day", "week", "month"], default="day")
    p.set_defaults(func=fuelmix_stacked, family="fuel_mix")

    p = fuel_sub.add_parser("selected")
    add_common_plot_args(p)
    p.add_argument("--settlemnt-type", default="INITIAL")
    p.add_argument("--bucket", choices=["hour", "day", "week", "month"], default="day")
    p.add_argument("--fuels", nargs="+", required=True)
    p.set_defaults(func=fuelmix_selected, family="fuel_mix")

    p = fuel_sub.add_parser("yoy")
    add_common_plot_args(p)
    p.add_argument("--settlement-type", default="INITIAL")
    p.add_argument("--fuels", nargs="+", required=True)
    p.set_defaults(func=fuelmix_yoy, family="fuel_mix")

    # 4) Outages
    outages = sub.add_parser("outages")
    outages_sub = outages.add_subparsers(dest="mode", required=True)

    p = outages_sub.add_parser("monthly")
    add_common_plot_args(p)
    p.set_defaults(func=outages_monthly, family="outages")

    p = outages_sub.add_parser("seasonal")
    add_common_plot_args(p)
    p.add_argument("--left-start", required=True)
    p.add_argument("--left-end", required=True)
    p.add_argument("--right-start", required=True)
    p.add_argument("--right-end", required=True)
    p.add_argument("--left-label")
    p.add_argument("--right-label")
    p.set_defaults(func=outages_seasonal, family="outages")

    p = outages_sub.add_parser("worst")
    add_common_plot_args(p)
    p.add_argument("--top-n", type=int, default=20)
    p.set_defaults(func=outages_worst, family="outages")

    # 5) Capacity vs demand
    reserve = sub.add_parser("reserve")
    add_common_plot_args(reserve)
    reserve.add_argument("--region", default="ERCOT")
    reserve.add_argument("--bucket", choices=["hour", "day", "week", "month"], default="day")
    reserve.set_defaults(func=reserve_plot, family="reserve")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    with connect(args.db) as conn:
        args.func(conn, args)


if __name__ == "__main__":
    main()
