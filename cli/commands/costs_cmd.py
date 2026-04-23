"""Cost tracking commands."""

import sys
from typing import Any

import click

from ..config import GCOConfig, _load_cdk_json
from ..output import get_output_formatter

pass_config = click.make_pass_decorator(GCOConfig, ensure=True)


def _get_deployment_regions(config: GCOConfig) -> list[str]:
    """Get the list of regional deployment regions from cdk.json or fallback to default."""
    cdk_regions = _load_cdk_json()
    if cdk_regions and "regional" in cdk_regions:
        regional = cdk_regions["regional"]
        if isinstance(regional, list) and all(isinstance(r, str) for r in regional):
            return regional
    return [config.default_region]


@click.group()
@pass_config
def costs(config: Any) -> None:
    """View cost breakdowns and estimates for GCO resources."""
    pass


@costs.command("summary")
@click.option(
    "--days", "-d", default=30, type=int, help="Number of days to look back (default: 30)"
)
@click.option(
    "--all", "show_all", is_flag=True, help="Show all account costs (not filtered by GCO tag)"
)
@pass_config
def costs_summary(config: Any, days: Any, show_all: Any) -> None:
    """Show total GCO spend by service.

    Examples:
        gco costs summary
        gco costs summary --days 7
        gco costs summary --all    # All account costs (useful before tags propagate)
    """
    from ..costs import get_cost_tracker

    formatter = get_output_formatter(config)

    try:
        tracker = get_cost_tracker(config)
        summary = tracker.get_cost_summary(days=days, unfiltered=show_all)
        label = "Account" if show_all else "GCO"

        if config.output_format != "table":
            formatter.print(
                {
                    "total": summary.total,
                    "currency": summary.currency,
                    "period_start": summary.period_start,
                    "period_end": summary.period_end,
                    "by_service": [
                        {"service": s.service, "amount": s.amount} for s in summary.by_service
                    ],
                }
            )
            return

        print(f"\n  {label} Cost Summary ({summary.period_start} to {summary.period_end})")
        print("  " + "-" * 75)
        print(f"  {'SERVICE':<50} {'COST':>12}")
        print("  " + "-" * 75)

        for svc in summary.by_service:
            print(f"  {svc.service:<50} ${svc.amount:>10.2f}")

        print("  " + "-" * 75)
        print(f"  {'TOTAL':<50} ${summary.total:>10.2f}")
        print()

    except Exception as e:
        formatter.print_error(f"Failed to get cost summary: {e}")
        sys.exit(1)


@costs.command("regions")
@click.option(
    "--days", "-d", default=30, type=int, help="Number of days to look back (default: 30)"
)
@pass_config
def costs_regions(config: Any, days: Any) -> None:
    """Show cost breakdown by region.

    Examples:
        gco costs regions
        gco costs regions --days 7
    """
    from ..costs import get_cost_tracker

    formatter = get_output_formatter(config)

    try:
        tracker = get_cost_tracker(config)
        by_region = tracker.get_cost_by_region(days=days)

        if config.output_format != "table":
            formatter.print(by_region)
            return

        total = sum(by_region.values())
        print(f"\n  GCO Cost by Region (last {days} days)")
        print("  " + "-" * 50)
        print(f"  {'REGION':<30} {'COST':>12}")
        print("  " + "-" * 50)

        for region, amount in by_region.items():
            pct = (amount / total * 100) if total > 0 else 0
            print(f"  {region:<30} ${amount:>10.2f}  ({pct:.0f}%)")

        print("  " + "-" * 50)
        print(f"  {'TOTAL':<30} ${total:>10.2f}")
        print()

    except Exception as e:
        formatter.print_error(f"Failed to get regional costs: {e}")
        sys.exit(1)


@costs.command("trend")
@click.option("--days", "-d", default=14, type=int, help="Number of days (default: 14)")
@click.option(
    "--all", "show_all", is_flag=True, help="Show all account costs (not filtered by GCO tag)"
)
@pass_config
def costs_trend(config: Any, days: Any, show_all: Any) -> None:
    """Show daily cost trend.

    Examples:
        gco costs trend
        gco costs trend --days 7
        gco costs trend --all
    """
    from ..costs import get_cost_tracker

    formatter = get_output_formatter(config)

    try:
        tracker = get_cost_tracker(config)
        trend = tracker.get_daily_trend(days=days, unfiltered=show_all)
        label = "Account" if show_all else "GCO"

        if config.output_format != "table":
            formatter.print(trend)
            return

        print(f"\n  Daily Cost Trend — {label} (last {days} days)")
        print("  " + "-" * 45)
        print(f"  {'DATE':<15} {'COST':>10}  {'CHART'}")
        print("  " + "-" * 45)

        max_amount = max((d["amount"] for d in trend), default=1) or 1
        for day in trend:
            bar_len = int(day["amount"] / max_amount * 25)
            bar = "█" * bar_len
            print(f"  {day['date']:<15} ${day['amount']:>8.2f}  {bar}")

        total = sum(d["amount"] for d in trend)
        avg = total / len(trend) if trend else 0
        print("  " + "-" * 45)
        print(f"  Total: ${total:.2f}  |  Avg/day: ${avg:.2f}")
        print()

    except Exception as e:
        formatter.print_error(f"Failed to get cost trend: {e}")
        sys.exit(1)


@costs.command("workloads")
@click.option("--region", "-r", help="Region to check (default: all deployment regions)")
@pass_config
def costs_workloads(config: Any, region: Any) -> None:
    """Estimate costs for running workloads (jobs and inference endpoints).

    Examples:
        gco costs workloads
        gco costs workloads -r us-east-1
    """
    from ..costs import get_cost_tracker

    formatter = get_output_formatter(config)

    try:
        tracker = get_cost_tracker(config)

        regions = [region] if region else _get_deployment_regions(config)
        all_workloads = []

        for r in regions:
            workloads = tracker.estimate_running_workloads(r)
            all_workloads.extend(workloads)

        if config.output_format != "table":
            formatter.print(
                [
                    {
                        "name": w.name,
                        "type": w.workload_type,
                        "instance_type": w.instance_type,
                        "gpu_count": w.gpu_count,
                        "hourly_rate": w.hourly_rate,
                        "runtime_hours": w.runtime_hours,
                        "estimated_cost": w.estimated_cost,
                        "region": w.region,
                    }
                    for w in all_workloads
                ]
            )
            return

        if not all_workloads:
            formatter.print_info("No running workloads found")
            return

        print(f"\n  Running Workload Costs ({len(all_workloads)} workloads)")
        print("  " + "-" * 95)
        print(
            f"  {'NAME':<30} {'TYPE':<10} {'INSTANCE':<15} {'GPU':>3} {'$/HR':>8} {'HOURS':>7} {'COST':>10}"
        )
        print("  " + "-" * 95)

        total = 0.0
        for w in sorted(all_workloads, key=lambda x: x.estimated_cost, reverse=True):
            name = w.name[:29]
            print(
                f"  {name:<30} {w.workload_type:<10} {w.instance_type:<15} "
                f"{w.gpu_count:>3} ${w.hourly_rate:>7.3f} {w.runtime_hours:>7.1f} ${w.estimated_cost:>9.4f}"
            )
            total += w.estimated_cost

        total_hourly = sum(w.hourly_rate for w in all_workloads)
        print("  " + "-" * 95)
        print(
            f"  {'TOTAL':<30} {'':10} {'':15} {'':>3} ${total_hourly:>7.3f} {'':>7} ${total:>9.4f}"
        )
        print()

    except Exception as e:
        formatter.print_error(f"Failed to estimate workload costs: {e}")
        sys.exit(1)


@costs.command("forecast")
@click.option("--days", "-d", default=30, type=int, help="Days to forecast (default: 30)")
@pass_config
def costs_forecast(config: Any, days: Any) -> None:
    """Forecast GCO costs for the next N days.

    Examples:
        gco costs forecast
        gco costs forecast --days 60
    """
    from ..costs import get_cost_tracker

    formatter = get_output_formatter(config)

    try:
        tracker = get_cost_tracker(config)
        forecast = tracker.get_forecast(days_ahead=days)

        if "error" in forecast:
            formatter.print_error(f"Forecast unavailable: {forecast['error']}")
            formatter.print_info("Cost Explorer needs 14+ days of data to generate forecasts")
            return

        if config.output_format != "table":
            formatter.print(forecast)
            return

        total = forecast.get("forecast_total", 0)
        print(f"\n  Cost Forecast ({forecast['period_start']} to {forecast['period_end']})")
        print("  " + "-" * 40)
        print(f"  Projected spend:  ${total:>10.2f}")
        print(f"  Daily average:    ${total / days:>10.2f}")
        print()

    except Exception as e:
        formatter.print_error(f"Failed to get forecast: {e}")
        sys.exit(1)
