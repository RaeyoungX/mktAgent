#!/usr/bin/env python3
"""
APScheduler daemon for mktAgent.
Loads jobs from config/schedule.yaml and runs them on cron schedules.

Run:
  python scheduler.py
  nohup python scheduler.py &> logs/scheduler.log &
"""

import logging
import sys
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/scheduler.log"),
    ],
)
logger = logging.getLogger("scheduler")


def make_job_fn(campaign_id: str, agents: list[str]):
    """Factory: returns a function that runs a campaign with specified agents."""
    def job():
        from db.database import get_session, init_db
        from agents.cmo_agent import CMOAgent
        init_db()
        db = get_session()
        try:
            cmo = CMOAgent(db)
            logger.info("Running job: campaign=%s agents=%s", campaign_id, agents)
            cmo.orchestrate(campaign_id, agents=agents if agents != ["all"] else None)
            logger.info("Job complete: campaign=%s", campaign_id)
        except Exception as exc:
            logger.error("Job failed: campaign=%s error=%s", campaign_id, exc, exc_info=True)
        finally:
            db.close()
    return job


def start():
    """Load schedule.yaml and start the blocking scheduler."""
    schedule_path = Path("config/schedule.yaml")
    if not schedule_path.exists():
        logger.error("config/schedule.yaml not found")
        sys.exit(1)

    cfg = yaml.safe_load(schedule_path.read_text())
    jobs = cfg.get("jobs", [])

    if not jobs:
        logger.warning("No jobs configured in schedule.yaml")
        return

    sched = BlockingScheduler(timezone="UTC")

    for job in jobs:
        name = job["name"]
        cron = job["cron"]
        campaign_id = job["campaign_id"]
        agents = job.get("agents", ["all"])

        # Parse cron string: "min hour day month dow"
        parts = cron.split()
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
        else:
            logger.warning("Invalid cron for job %s: %s", name, cron)
            continue

        trigger = CronTrigger(
            minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week
        )
        sched.add_job(
            make_job_fn(campaign_id, agents),
            trigger=trigger,
            id=name,
            name=name,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Scheduled: %s @ %s → campaign=%s agents=%s", name, cron, campaign_id, agents)

    logger.info("Scheduler started with %d jobs. Press Ctrl+C to stop.", len(jobs))
    try:
        sched.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    start()
