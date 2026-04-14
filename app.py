#!/usr/bin/env python3
"""
mktAgent Desktop App — NiceGUI native macOS window.

Run:
  python app.py
"""

import asyncio
import logging
import queue
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from nicegui import app, run, ui

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(override=True)

from db.database import get_session, init_db
from db.models import (
    AccountHealth as DBAccountHealth,
    Campaign,
    ContentPiece as DBContentPiece,
    PostMetrics as DBMetrics,
    Product,
)

# ─── Logging → queue bridge ───────────────────────────────────────────────────

class QueueLogHandler(logging.Handler):
    """Pushes log records into a queue for NiceGUI ui.log consumption."""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put_nowait(self.format(record))
        except Exception:
            pass


# ─── Agent runner state ───────────────────────────────────────────────────────

_running: dict[str, bool] = {}       # product_id → bool


# ─── DB helpers ───────────────────────────────────────────────────────────────

def all_products() -> list[Product]:
    db = get_session()
    try:
        return db.query(Product).order_by(Product.created_at.desc()).all()
    finally:
        db.close()


def get_product(product_id: str) -> Optional[Product]:
    db = get_session()
    try:
        return db.query(Product).filter(Product.id == product_id).first()
    finally:
        db.close()


def create_product(name: str, url: str) -> Product:
    db = get_session()
    try:
        p = Product(
            id=str(uuid.uuid4()),
            name=name,
            url=url,
            enabled_platforms_json={
                "reddit": {"enabled": False, "accounts": [], "subreddits": []},
                "xhs": {"enabled": False},
                "twitter": {"enabled": False},
                "linkedin": {"enabled": False},
            },
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return p
    finally:
        db.close()


def delete_product(product_id: str):
    db = get_session()
    try:
        db.query(Product).filter(Product.id == product_id).delete()
        db.commit()
    finally:
        db.close()


def save_product_platforms(product_id: str, platforms: dict):
    db = get_session()
    try:
        p = db.query(Product).filter(Product.id == product_id).first()
        if p:
            p.enabled_platforms_json = platforms
            db.commit()
    finally:
        db.close()


def get_content(product_id: str) -> list[DBContentPiece]:
    db = get_session()
    try:
        return db.query(DBContentPiece).filter(
            DBContentPiece.campaign_id == product_id
        ).order_by(DBContentPiece.created_at.desc()).all()
    finally:
        db.close()


def approve_piece(content_id: str):
    db = get_session()
    try:
        p = db.query(DBContentPiece).filter(DBContentPiece.id == content_id).first()
        if p:
            p.status = "approved"
            db.commit()
    finally:
        db.close()


def delete_piece(content_id: str):
    db = get_session()
    try:
        db.query(DBContentPiece).filter(DBContentPiece.id == content_id).delete()
        db.commit()
    finally:
        db.close()


def get_account_health(product_id: str) -> list[DBAccountHealth]:
    db = get_session()
    try:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return []
        platforms = product.enabled_platforms_json or {}
        accounts = []
        for platform, cfg in platforms.items():
            for username in (cfg.get("accounts") or []):
                row = db.query(DBAccountHealth).filter(
                    DBAccountHealth.platform == platform,
                    DBAccountHealth.username == username,
                ).first()
                if row:
                    accounts.append(row)
        return accounts
    finally:
        db.close()


def get_metrics_summary(product_id: str) -> dict:
    """Return simple aggregated metrics per platform."""
    db = get_session()
    try:
        pieces = db.query(DBContentPiece).filter(
            DBContentPiece.campaign_id == product_id,
            DBContentPiece.status == "posted",
        ).all()

        summary: dict[str, dict] = {}
        for piece in pieces:
            platform = piece.platform
            if platform not in summary:
                summary[platform] = {"posts": 0, "total_engagement": 0, "top_url": ""}
            summary[platform]["posts"] += 1
            latest = db.query(DBMetrics).filter(
                DBMetrics.content_id == piece.id
            ).order_by(DBMetrics.checked_at.desc()).first()
            if latest:
                eng = latest.likes + latest.upvotes + latest.comments + latest.shares
                summary[platform]["total_engagement"] += eng
        return summary
    finally:
        db.close()


# ─── Shared UI components ─────────────────────────────────────────────────────

PLATFORM_ICONS = {
    "reddit": "🤖",
    "xhs": "📕",
    "twitter": "🐦",
    "linkedin": "💼",
}

PHASE_COLORS = {
    "lurk": "gray",
    "warmup": "orange",
    "promo": "green",
}

STATUS_COLORS = {
    "draft": "text-gray-400",
    "approved": "text-green-600 font-bold",
    "posted": "text-blue-600",
    "failed": "text-red-500",
}


def status_chip(status: str):
    color_map = {
        "draft": "bg-gray-100 text-gray-600",
        "approved": "bg-green-100 text-green-700",
        "posted": "bg-blue-100 text-blue-700",
        "failed": "bg-red-100 text-red-600",
    }
    classes = color_map.get(status, "bg-gray-100 text-gray-600")
    ui.label(status).classes(f"text-xs px-2 py-0.5 rounded-full {classes}")


# ─── Add Product Dialog ───────────────────────────────────────────────────────

def show_add_product_dialog(on_added=None):
    with ui.dialog() as dialog, ui.card().classes("w-[500px] p-6"):
        ui.label("添加新产品").classes("text-lg font-bold mb-4")

        url_input = ui.input(
            label="产品网址",
            placeholder="https://yourproduct.com",
            validation={"请输入有效 URL": lambda v: v.startswith("http")},
        ).classes("w-full")

        name_input = ui.input(
            label="产品名称（可留空，自动从页面提取）",
            placeholder="My Product",
        ).classes("w-full mt-2")

        status_label = ui.label("").classes("text-sm text-gray-500 mt-2")

        async def do_add():
            url = url_input.value.strip()
            if not url.startswith("http"):
                ui.notify("请输入有效的产品网址", type="negative")
                return

            name = name_input.value.strip() or url.split("//")[-1].split("/")[0]
            status_label.set_text("创建中…")

            p = create_product(name, url)
            status_label.set_text(f"✓ 已创建: {p.name}")
            dialog.close()
            ui.notify(f"产品已添加: {p.name}", type="positive")
            if on_added:
                on_added()

        with ui.row().classes("mt-4 justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("添加", on_click=do_add).props("color=primary")

    dialog.open()


# ─── Page: Products Home ──────────────────────────────────────────────────────

@ui.page("/")
async def products_page():
    ui.query("body").classes(add="bg-gray-50")

    with ui.header(elevated=True).classes("bg-blue-700 text-white px-6 py-3 items-center justify-between"):
        ui.label("🚀 mktAgent").classes("text-xl font-bold tracking-tight")
        ui.label("通用产品营销自动化").classes("text-blue-200 text-sm")

    with ui.column().classes("p-8 w-full max-w-5xl mx-auto"):
        with ui.row().classes("w-full justify-between items-center mb-6"):
            ui.label("我的产品").classes("text-2xl font-bold text-gray-800")
            ui.button("+ 添加产品", on_click=lambda: show_add_product_dialog(on_added=refresh)).props("color=primary")

        products_container = ui.column().classes("w-full gap-4")

        def refresh():
            products_container.clear()
            products = all_products()
            if not products:
                with products_container:
                    with ui.card().classes("w-full p-12 text-center border-2 border-dashed border-gray-200"):
                        ui.icon("rocket_launch", size="3rem").classes("text-gray-300 mx-auto")
                        ui.label("还没有产品").classes("text-gray-400 mt-2")
                        ui.label("点击「+ 添加产品」开始").classes("text-gray-300 text-sm")
                return

            with products_container:
                for p in products:
                    _product_card(p, on_delete=refresh)

        refresh()


def _product_card(p: Product, on_delete=None):
    platforms = p.enabled_platforms_json or {}
    enabled = [k for k, v in platforms.items() if v.get("enabled")]

    with ui.card().classes("w-full hover:shadow-md transition-shadow cursor-pointer"):
        with ui.row().classes("w-full items-start justify-between"):
            with ui.column().classes("gap-1"):
                ui.link(p.name, f"/product/{p.id}").classes("text-lg font-semibold text-blue-700 no-underline hover:underline")
                ui.label(p.url).classes("text-sm text-gray-400")
                if p.description:
                    ui.label(p.description[:100] + "…" if len(p.description or "") > 100 else p.description).classes("text-sm text-gray-600 mt-1")

            with ui.column().classes("items-end gap-1"):
                with ui.row().classes("gap-1"):
                    for platform in enabled:
                        ui.label(PLATFORM_ICONS.get(platform, "🌐") + " " + platform).classes("text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full")

                if p.last_run_at:
                    ui.label(f"上次运行: {p.last_run_at.strftime('%m-%d %H:%M')}").classes("text-xs text-gray-400")

                ui.button(icon="delete", on_click=lambda pid=p.id: _confirm_delete(pid, on_delete)).props("flat dense color=red-4")


def _confirm_delete(product_id: str, on_delete=None):
    with ui.dialog() as d, ui.card():
        ui.label("确认删除此产品？此操作不可撤销。").classes("mb-4")
        with ui.row().classes("gap-2 justify-end"):
            ui.button("取消", on_click=d.close).props("flat")
            def do_delete():
                delete_product(product_id)
                d.close()
                ui.notify("已删除", type="warning")
                if on_delete:
                    on_delete()
            ui.button("删除", on_click=do_delete).props("color=negative")
    d.open()


# ─── Page: Product Detail ─────────────────────────────────────────────────────

@ui.page("/product/{product_id}")
async def product_page(product_id: str):
    ui.query("body").classes(add="bg-gray-50")

    p = get_product(product_id)
    if not p:
        ui.label("产品未找到").classes("p-8 text-red-500")
        return

    with ui.header(elevated=True).classes("bg-blue-700 text-white px-6 py-3 items-center gap-4"):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props("flat color=white dense")
        ui.label(p.name).classes("text-lg font-bold")
        ui.label(p.url).classes("text-blue-200 text-sm")

    with ui.column().classes("p-6 w-full max-w-6xl mx-auto"):
        with ui.tabs().classes("w-full") as tabs:
            tab_run = ui.tab("▶ 运行 Agent")
            tab_strategy = ui.tab("📡 渠道策略")
            tab_content = ui.tab("📝 内容审批")
            tab_platforms = ui.tab("⚙ 平台配置")
            tab_accounts = ui.tab("👤 账号健康")
            tab_dashboard = ui.tab("📊 数据看板")

        with ui.tab_panels(tabs, value=tab_run).classes("w-full mt-2"):
            with ui.tab_panel(tab_run):
                _tab_run(product_id, p)
            with ui.tab_panel(tab_strategy):
                _tab_strategy(product_id)
            with ui.tab_panel(tab_content):
                _tab_content(product_id)
            with ui.tab_panel(tab_platforms):
                _tab_platforms(product_id, p)
            with ui.tab_panel(tab_accounts):
                _tab_accounts(product_id)
            with ui.tab_panel(tab_dashboard):
                _tab_dashboard(product_id)


# ─── Tab: Run Agent ───────────────────────────────────────────────────────────

def _tab_run(product_id: str, p: Product):
    log_q: queue.Queue = queue.Queue()

    with ui.row().classes("w-full gap-4"):
        # Left: controls
        with ui.card().classes("p-4 w-72 shrink-0"):
            ui.label("选择要运行的 Agent").classes("font-semibold mb-2")

            agent_checks = {}
            agents = [
                ("product_analysis", "🔍 产品分析"),
                ("channel", "📡 渠道策略"),
                ("content", "✍ 内容生成"),
                ("account_cultivation", "🌱 账号养号"),
                ("distribution", "🚀 内容分发（仅已审批）"),
                ("feedback", "📈 效果反馈"),
            ]
            for key, label in agents:
                agent_checks[key] = ui.checkbox(label, value=(key in ("product_analysis", "channel", "content")))

            ui.separator().classes("my-3")
            run_btn = ui.button("▶ 开始运行", icon="play_arrow").props("color=primary").classes("w-full")
            status_label = ui.label("").classes("text-sm text-gray-500 mt-2 text-center")

        # Right: live log
        with ui.card().classes("p-4 flex-1"):
            ui.label("实时日志").classes("font-semibold mb-2")
            log_elem = ui.log(max_lines=300).classes("w-full h-96 font-mono text-xs bg-gray-900 text-green-400 rounded")

    def drain_queue():
        while not log_q.empty():
            try:
                log_elem.push(log_q.get_nowait())
            except Exception:
                break

    drain_timer = ui.timer(0.15, drain_queue)

    async def do_run():
        if _running.get(product_id):
            ui.notify("已有 agent 在运行中", type="warning")
            return

        selected = [k for k, chk in agent_checks.items() if chk.value]
        if not selected:
            ui.notify("请至少选择一个 agent", type="warning")
            return

        _running[product_id] = True
        run_btn.disable()
        status_label.set_text("运行中…")

        handler = QueueLogHandler(log_q)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

        try:
            def _sync_run():
                from agents.cmo_agent import CMOAgent
                db = get_session()
                try:
                    cmo = CMOAgent(db)
                    cmo.orchestrate(product_id, agents=selected)
                    # Update last_run_at
                    prod = db.query(Product).filter(Product.id == product_id).first()
                    if prod:
                        prod.last_run_at = datetime.utcnow()
                        db.commit()
                finally:
                    db.close()

            await run.io_bound(_sync_run)
            status_label.set_text("✓ 完成")
            ui.notify("Agent 运行完成！", type="positive")
        except Exception as exc:
            status_label.set_text(f"✗ 出错: {exc}")
            ui.notify(f"运行出错: {exc}", type="negative")
        finally:
            _running[product_id] = False
            run_btn.enable()
            root_logger.removeHandler(handler)

    run_btn.on_click(do_run)


# ─── Tab: Content Approval ────────────────────────────────────────────────────

def _tab_content(product_id: str):
    container = ui.column().classes("w-full gap-3")

    def refresh():
        container.clear()
        pieces = get_content(product_id)
        if not pieces:
            with container:
                ui.label("暂无内容 — 先运行「内容生成」agent").classes("text-gray-400 p-4")
            return

        # Group by status
        draft = [p for p in pieces if p.status == "draft"]
        approved = [p for p in pieces if p.status == "approved"]
        posted = [p for p in pieces if p.status == "posted"]
        failed = [p for p in pieces if p.status == "failed"]

        with container:
            if draft:
                ui.label(f"待审批 ({len(draft)})").classes("font-semibold text-gray-700 mt-2")
                for piece in draft:
                    _content_card(piece, refresh)

            if approved:
                ui.label(f"已审批·待发布 ({len(approved)})").classes("font-semibold text-green-700 mt-4")
                for piece in approved:
                    _content_card(piece, refresh)

            if posted:
                ui.label(f"已发布 ({len(posted)})").classes("font-semibold text-blue-700 mt-4")
                for piece in posted:
                    _content_card(piece, refresh)

            if failed:
                ui.label(f"发布失败 ({len(failed)})").classes("font-semibold text-red-600 mt-4")
                for piece in failed:
                    _content_card(piece, refresh)

    refresh()


def _content_card(piece: DBContentPiece, on_change=None):
    with ui.card().classes("w-full p-4"):
        with ui.row().classes("w-full items-start justify-between gap-3"):
            # Left: content
            with ui.column().classes("flex-1 gap-1 min-w-0"):
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label(PLATFORM_ICONS.get(piece.platform, "🌐") + " " + piece.platform).classes(
                        "text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full")
                    ui.label(piece.content_type).classes("text-xs text-gray-400")
                    status_chip(piece.status)
                    if piece.warmup_mode:
                        ui.label("养号模式").classes("text-xs bg-yellow-50 text-yellow-600 px-2 py-0.5 rounded-full")

                if piece.title:
                    ui.label(piece.title).classes("font-semibold text-gray-800 mt-1")
                ui.label(piece.body[:300] + ("…" if len(piece.body) > 300 else "")).classes(
                    "text-sm text-gray-600 whitespace-pre-wrap")

                if piece.hashtags_json:
                    ui.label(" ".join(f"#{t}" for t in piece.hashtags_json[:5])).classes("text-xs text-blue-400 mt-1")

                if piece.post_url:
                    ui.link("查看发布链接 →", piece.post_url, new_tab=True).classes("text-xs text-blue-500 mt-1")

            # Right: actions
            with ui.column().classes("items-end gap-2 shrink-0"):
                if piece.status == "draft":
                    ui.button("✓ 审批", on_click=lambda cid=piece.id: [approve_piece(cid), on_change() if on_change else None]).props(
                        "color=positive size=sm")
                    ui.button("✗ 删除", on_click=lambda cid=piece.id: [delete_piece(cid), on_change() if on_change else None]).props(
                        "color=negative flat size=sm")
                elif piece.status == "approved":
                    ui.button("✗ 撤销", on_click=lambda cid=piece.id: _unapprove(cid, on_change)).props(
                        "color=warning flat size=sm")


def _unapprove(content_id: str, on_change=None):
    db = get_session()
    try:
        p = db.query(DBContentPiece).filter(DBContentPiece.id == content_id).first()
        if p:
            p.status = "draft"
            db.commit()
    finally:
        db.close()
    if on_change:
        on_change()


# ─── Tab: Channel Strategy ────────────────────────────────────────────────────

def _tab_strategy(product_id: str):
    from db.models import ChannelStrategy as DBStrategy, ProductAnalysis as DBAnalysis
    db = get_session()

    # Latest product analysis
    analysis_row = db.query(DBAnalysis).filter(
        DBAnalysis.campaign_id == product_id
    ).order_by(DBAnalysis.id.desc()).first()

    # Latest strategy
    strategy_row = db.query(DBStrategy).filter(
        DBStrategy.campaign_id == product_id
    ).order_by(DBStrategy.version.desc()).first()

    if not analysis_row and not strategy_row:
        with ui.column().classes("w-full items-center py-12 gap-3"):
            ui.icon("analytics", size="3rem").classes("text-gray-300")
            ui.label("还没有策略，先运行 Agent 分析产品").classes("text-gray-400")
        return

    # Product analysis card
    if analysis_row:
        a = analysis_row.analysis_json
        with ui.card().classes("w-full p-5 mb-4"):
            ui.label("产品分析").classes("font-bold text-base mb-3")
            with ui.grid(columns=2).classes("w-full gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label("产品名称").classes("text-xs text-gray-400 uppercase tracking-wide")
                    ui.label(a.get("product_name", "—")).classes("font-medium")
                with ui.column().classes("gap-1"):
                    ui.label("定价层级").classes("text-xs text-gray-400 uppercase tracking-wide")
                    ui.label(a.get("pricing_tier", "—")).classes("font-medium")
            ui.separator().classes("my-3")
            ui.label("描述").classes("text-xs text-gray-400 uppercase tracking-wide mb-1")
            ui.label(a.get("description", "—")).classes("text-sm")
            if a.get("key_features"):
                ui.separator().classes("my-3")
                ui.label("核心功能").classes("text-xs text-gray-400 uppercase tracking-wide mb-2")
                with ui.row().classes("flex-wrap gap-2"):
                    for f in a.get("key_features", []):
                        ui.badge(f, color="blue").classes("text-xs")
            if a.get("content_themes"):
                ui.separator().classes("my-3")
                ui.label("内容主题").classes("text-xs text-gray-400 uppercase tracking-wide mb-2")
                with ui.row().classes("flex-wrap gap-2"):
                    for t in a.get("content_themes", []):
                        ui.badge(t, color="green").classes("text-xs")
            if a.get("pain_points"):
                ui.separator().classes("my-3")
                ui.label("用户痛点").classes("text-xs text-gray-400 uppercase tracking-wide mb-2")
                for pt in a.get("pain_points", []):
                    with ui.row().classes("items-start gap-1"):
                        ui.label("•").classes("text-red-400 mt-0.5")
                        ui.label(pt).classes("text-sm")

    # Channel strategy card
    if strategy_row:
        s = strategy_row.strategy_json
        with ui.card().classes("w-full p-5"):
            with ui.row().classes("items-center justify-between mb-3"):
                ui.label("渠道策略").classes("font-bold text-base")
                ui.badge(f"v{strategy_row.version}", color="purple")
            ui.label("整体叙事").classes("text-xs text-gray-400 uppercase tracking-wide mb-1")
            ui.label(s.get("overall_narrative", "—")).classes("text-sm italic mb-4")
            ui.separator().classes("mb-4")
            FREQ_COLOR = {"5x_per_week": "red", "4x_per_week": "orange", "3x_per_week": "green", "2x_per_week": "blue", "1x_per_week": "gray"}
            for plat in s.get("platforms", []):
                if not plat.get("enabled"):
                    continue
                icon = PLATFORM_ICONS.get(plat["platform"], "📣")
                with ui.expansion(f"{icon} {plat['platform'].upper()}  ·  {plat.get('posting_frequency','')}").classes("w-full mb-2"):
                    with ui.grid(columns=2).classes("gap-3 mt-2"):
                        with ui.column().classes("gap-1"):
                            ui.label("语调").classes("text-xs text-gray-400")
                            ui.label(plat.get("tone", "—")).classes("text-sm")
                        with ui.column().classes("gap-1"):
                            ui.label("内容格式").classes("text-xs text-gray-400")
                            ui.label(", ".join(plat.get("content_formats", []))).classes("text-sm")
                    if plat.get("subreddits"):
                        ui.label("目标 Subreddits").classes("text-xs text-gray-400 mt-2")
                        with ui.row().classes("flex-wrap gap-1 mt-1"):
                            for sr in plat["subreddits"]:
                                ui.badge(sr, color="orange").classes("text-xs")
                    if plat.get("hashtags"):
                        ui.label("Hashtags").classes("text-xs text-gray-400 mt-2")
                        with ui.row().classes("flex-wrap gap-1 mt-1"):
                            for h in plat["hashtags"]:
                                ui.badge(h, color="teal").classes("text-xs")
                    if plat.get("best_times"):
                        ui.label("最佳发帖时间").classes("text-xs text-gray-400 mt-2")
                        ui.label(", ".join(plat["best_times"])).classes("text-sm")


# ─── Tab: Platform Config ─────────────────────────────────────────────────────

def _tab_platforms(product_id: str, p: Product):
    platforms = dict(p.enabled_platforms_json or {})

    with ui.card().classes("p-6 w-full max-w-lg"):
        ui.label("渠道配置").classes("font-bold text-lg mb-4")
        controls: dict = {}

        for platform in ["reddit", "xhs", "twitter", "linkedin"]:
            cfg = platforms.get(platform, {})
            with ui.expansion(PLATFORM_ICONS.get(platform, "") + " " + platform.upper()).classes("w-full mb-2"):
                enabled = ui.switch("启用", value=cfg.get("enabled", False))
                accounts_input = ui.input(
                    "账号列表（逗号分隔）",
                    value=", ".join(cfg.get("accounts", [])),
                    placeholder="user1, user2",
                ).classes("w-full mt-2")
                if platform == "reddit":
                    subs_input = ui.input(
                        "目标 subreddit（逗号分隔）",
                        value=", ".join(cfg.get("subreddits", [])),
                        placeholder="r/travel, r/China",
                    ).classes("w-full mt-2")
                else:
                    subs_input = None
                controls[platform] = (enabled, accounts_input, subs_input)

        def save_config():
            new_platforms = {}
            for platform, (enabled_sw, acc_inp, sub_inp) in controls.items():
                accounts = [a.strip() for a in acc_inp.value.split(",") if a.strip()]
                cfg = {"enabled": enabled_sw.value, "accounts": accounts}
                if sub_inp:
                    cfg["subreddits"] = [s.strip() for s in sub_inp.value.split(",") if s.strip()]
                new_platforms[platform] = cfg
            save_product_platforms(product_id, new_platforms)
            ui.notify("配置已保存", type="positive")

        ui.button("保存配置", on_click=save_config).props("color=primary").classes("mt-4")


# ─── Tab: Account Health ──────────────────────────────────────────────────────

def _tab_accounts(product_id: str):
    accounts = get_account_health(product_id)
    if not accounts:
        ui.label("暂无账号数据 — 先运行「账号养号」agent").classes("text-gray-400 p-4")
        return

    columns = [
        {"name": "platform", "label": "平台", "field": "platform"},
        {"name": "username", "label": "账号", "field": "username"},
        {"name": "phase", "label": "阶段", "field": "phase"},
        {"name": "karma", "label": "Karma", "field": "karma"},
        {"name": "age", "label": "账号天数", "field": "age"},
        {"name": "last_session", "label": "上次运行", "field": "last_session"},
        {"name": "shadowbanned", "label": "被屏蔽", "field": "shadowbanned"},
    ]
    rows = []
    for a in accounts:
        rows.append({
            "platform": PLATFORM_ICONS.get(a.platform, "") + " " + a.platform,
            "username": a.username,
            "phase": a.warmup_phase or "lurk",
            "karma": a.karma or "—",
            "age": f"{a.account_age_days:.0f}天" if a.account_age_days else "—",
            "last_session": a.last_session_date or "从未",
            "shadowbanned": "⚠ 是" if a.is_shadowbanned else "否",
        })

    ui.table(columns=columns, rows=rows).classes("w-full")


# ─── Tab: Dashboard ───────────────────────────────────────────────────────────

def _tab_dashboard(product_id: str):
    db = get_session()
    try:
        total_pieces = db.query(DBContentPiece).filter(
            DBContentPiece.campaign_id == product_id
        ).count()
        posted = db.query(DBContentPiece).filter(
            DBContentPiece.campaign_id == product_id,
            DBContentPiece.status == "posted",
        ).count()
        approved = db.query(DBContentPiece).filter(
            DBContentPiece.campaign_id == product_id,
            DBContentPiece.status == "approved",
        ).count()
        draft = db.query(DBContentPiece).filter(
            DBContentPiece.campaign_id == product_id,
            DBContentPiece.status == "draft",
        ).count()
    finally:
        db.close()

    metrics = get_metrics_summary(product_id)

    with ui.row().classes("gap-4 w-full flex-wrap"):
        _stat_card("内容总量", str(total_pieces), "article")
        _stat_card("待审批", str(draft), "pending_actions", color="text-yellow-600")
        _stat_card("待发布", str(approved), "schedule_send", color="text-green-600")
        _stat_card("已发布", str(posted), "check_circle", color="text-blue-600")

    if metrics:
        ui.label("各平台数据").classes("font-semibold mt-6 mb-3")
        with ui.row().classes("gap-4 flex-wrap"):
            for platform, stats in metrics.items():
                _stat_card(
                    PLATFORM_ICONS.get(platform, "") + " " + platform,
                    f"{stats['posts']} 篇",
                    "bar_chart",
                    subtitle=f"互动总量: {stats['total_engagement']}",
                )
    else:
        ui.label("暂无发布数据").classes("text-gray-400 mt-4")


def _stat_card(title: str, value: str, icon: str, color: str = "text-gray-800", subtitle: str = ""):
    with ui.card().classes("p-4 min-w-[140px] text-center"):
        ui.icon(icon, size="2rem").classes(color + " mx-auto")
        ui.label(value).classes(f"text-2xl font-bold {color} mt-1")
        ui.label(title).classes("text-sm text-gray-500")
        if subtitle:
            ui.label(subtitle).classes("text-xs text-gray-400")


# ─── App startup ──────────────────────────────────────────────────────────────

init_db()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("logs/mktAgent.log"), logging.StreamHandler()],
)

ui.run(
    title="mktAgent",
    favicon="🚀",
    native=True,
    window_size=(1280, 860),
    reload=False,
)
