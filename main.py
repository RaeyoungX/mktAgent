#!/usr/bin/env python3
"""
mktAgent CLI — CMO-orchestrated marketing agent system.

Usage:
  python main.py run --campaign <id>
  python main.py analyze --url https://example.com
  python main.py content --campaign <id>
  python main.py post --campaign <id> [--platform reddit]
  python main.py feedback --campaign <id>
  python main.py accounts status [--campaign <id>]
  python main.py scheduler start
  python main.py scheduler list
"""

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import print as rprint

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

load_dotenv(override=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/mktAgent.log"),
    ],
)
logger = logging.getLogger("mktAgent")
console = Console()


def get_db():
    from db.database import get_session, init_db
    init_db()
    return get_session()


def get_cmo(db=None):
    from agents.cmo_agent import CMOAgent
    return CMOAgent(db or get_db())


# ─── CLI groups ──────────────────────────────────────────────────────────────

@click.group()
def cli():
    """mktAgent — AI-powered marketing automation."""
    pass


# ─── run ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--campaign", required=True, help="Campaign ID (matches config/products/*.yaml)")
@click.option("--agents", default=None, help="Comma-separated agents to run (default: all)")
def run(campaign, agents):
    """Run the full CMO campaign cycle (or specific agents)."""
    agent_list = [a.strip() for a in agents.split(",")] if agents else None
    console.print(f"[bold green]Starting campaign:[/bold green] {campaign}")
    cmo = get_cmo()
    cmo.orchestrate(campaign, agents=agent_list)
    console.print("[bold green]✓ Campaign cycle complete[/bold green]")


# ─── analyze ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--url", required=True, help="Product URL to analyze")
@click.option("--campaign", default="ad_hoc", help="Campaign ID to save analysis under")
def analyze(url, campaign):
    """Scrape a URL and run product analysis."""
    from agents.product_analysis_agent import ProductAnalysisAgent
    db = get_db()
    from db.database import init_db
    init_db()

    # Ensure campaign exists
    from db.models import Campaign
    if not db.query(Campaign).filter(Campaign.id == campaign).first():
        db.add(Campaign(id=campaign, product_url=url, product_name="ad_hoc"))
        db.commit()

    agent = ProductAnalysisAgent(db)
    console.print(f"[bold]Analyzing:[/bold] {url}")
    result = agent.run(url, campaign)

    console.print(f"\n[bold green]Product:[/bold green] {result.product_name}")
    console.print(f"[bold]Description:[/bold] {result.description}")
    console.print(f"[bold]Pricing:[/bold] {result.pricing_tier}")
    console.print(f"[bold]Target:[/bold] {result.target_audience.primary}")
    console.print(f"\n[bold]Key features:[/bold]")
    for f in result.key_features:
        console.print(f"  • {f}")
    console.print(f"\n[bold]Content themes:[/bold]")
    for t in result.content_themes:
        console.print(f"  • {t}")


# ─── content ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--campaign", required=True)
@click.option("--week", default=1, help="Week number for this batch")
def content(campaign, week):
    """Generate a content batch for the campaign."""
    db = get_db()
    cmo = CMOAgent = get_cmo(db)

    config = cmo.load_campaign_config(campaign)
    cmo._ensure_campaign_in_db(config)

    product = cmo._get_latest_product(campaign)
    if not product:
        console.print("[red]No product analysis found. Run analyze first.[/red]")
        return

    strategy = cmo._get_latest_strategy(campaign)
    if not strategy:
        console.print("[red]No channel strategy found. Run with --agents channel first.[/red]")
        return

    from agents.content_agent import ContentAgent
    agent = ContentAgent(db)
    batch = agent.run(strategy, product, campaign, week_number=week)

    console.print(f"\n[bold green]Generated {batch.total_pieces} content pieces[/bold green]")
    table = Table("Platform", "Type", "Title/Preview", "Warmup")
    for p in batch.pieces:
        preview = (p.title or p.body)[:50] + "..."
        table.add_row(p.platform, p.content_type, preview, "✓" if p.warmup_mode else "")
    console.print(table)


# ─── post ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--campaign", required=True)
@click.option("--platform", default=None, help="Filter to a specific platform")
@click.option("--dry-run", is_flag=True, help="Show what would be posted without posting")
def post(campaign, platform, dry_run):
    """Post pending content to platforms."""
    db = get_db()
    from agents.distribution_agent import DistributionAgent
    agent = DistributionAgent(db)

    if dry_run:
        console.print("[yellow]DRY RUN — nothing will be posted[/yellow]")

    results = agent.run(campaign, platform=platform, dry_run=dry_run)

    if not results:
        console.print("No pending content to post.")
        return

    table = Table("Platform", "Content ID", "Status", "URL")
    for r in results:
        status = "[green]✓ posted[/green]" if r.get("success") else f"[red]✗ {r.get('error', 'failed')[:30]}[/red]"
        table.add_row(r.get("platform", ""), r.get("content_id", "")[:8] + "...", status, r.get("url", "—"))
    console.print(table)


# ─── feedback ────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--campaign", required=True)
@click.option("--days", default=7, help="Number of days to look back")
def feedback(campaign, days):
    """Pull metrics and generate a feedback report."""
    db = get_db()
    from agents.feedback_agent import FeedbackAgent
    agent = FeedbackAgent(db)
    report = agent.run(campaign, days=days)

    console.print(f"\n[bold]Feedback Report[/bold]: {campaign}")
    console.print(f"Period: {report.period_start.date()} → {report.period_end.date()}")

    if report.what_worked:
        console.print("\n[bold green]What worked:[/bold green]")
        for w in report.what_worked:
            console.print(f"  ✓ {w}")

    if report.what_didnt:
        console.print("\n[bold red]What didn't:[/bold red]")
        for w in report.what_didnt:
            console.print(f"  ✗ {w}")

    if report.recommendations:
        console.print("\n[bold]Recommendations:[/bold]")
        for r in report.recommendations:
            console.print(f"  → {r}")


# ─── accounts ────────────────────────────────────────────────────────────────

@cli.group()
def accounts():
    """Account health management."""
    pass


@accounts.command("status")
@click.option("--campaign", default=None)
def accounts_status(campaign):
    """Show account health for all tracked accounts."""
    from db.models import AccountHealth
    db = get_db()
    rows = db.query(AccountHealth).all()
    if not rows:
        console.print("No accounts tracked yet.")
        return
    table = Table("Platform", "Username", "Phase", "Karma", "Age (days)", "Last Session", "Shadowbanned")
    for r in rows:
        sb = "[red]YES[/red]" if r.is_shadowbanned else "No"
        table.add_row(
            r.platform, r.username, r.warmup_phase or "—",
            str(r.karma or "—"), f"{r.account_age_days:.1f}" if r.account_age_days else "—",
            r.last_session_date or "never", sb
        )
    console.print(table)


# ─── scheduler ───────────────────────────────────────────────────────────────

@cli.group()
def scheduler():
    """Scheduler management."""
    pass


@scheduler.command("start")
def scheduler_start():
    """Start the APScheduler cron daemon."""
    console.print("[bold]Starting scheduler...[/bold]")
    import scheduler as sched_module
    sched_module.start()


@scheduler.command("list")
def scheduler_list():
    """List configured scheduled jobs."""
    import yaml
    cfg = yaml.safe_load(Path("config/schedule.yaml").read_text())
    table = Table("Name", "Cron", "Campaign", "Agents")
    for job in cfg.get("jobs", []):
        table.add_row(
            job["name"], job["cron"], job["campaign_id"],
            ", ".join(job.get("agents", ["all"]))
        )
    console.print(table)


if __name__ == "__main__":
    cli()
