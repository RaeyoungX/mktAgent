"""
Reddit browser tool — JS injection into real Chrome via AppleScript.
Same-origin fetch through reddit.com logged-in session.
Pattern ported directly from reddit-cultivate/SKILL.md.
"""

import logging
import time
from typing import Optional
from tools.chrome import chrome_js_fetch, chrome_nav, short_delay

logger = logging.getLogger(__name__)

REDDIT_HOME = "https://www.reddit.com"


def ensure_on_reddit():
    """Navigate to reddit.com if not already there."""
    chrome_nav(REDDIT_HOME, wait=2.0)


def get_me() -> Optional[dict]:
    """
    Fetch /api/me.json — returns user info including karma and modhash.
    Result keys: name, total_karma, comment_karma, link_karma, created_utc, modhash
    """
    ensure_on_reddit()
    js = """
(function() {
    fetch("/api/me.json", {credentials: "include"})
        .then(r => r.json())
        .then(d => {
            document.title = "ME:" + JSON.stringify({
                name: d.data.name,
                karma: d.data.total_karma,
                comment_karma: d.data.comment_karma,
                link_karma: d.data.link_karma,
                created_utc: d.data.created_utc,
                modhash: d.data.modhash
            });
        })
        .catch(e => { document.title = "ME:ERROR:" + e.message; });
})();
"""
    return chrome_js_fetch(js, result_prefix="ME:")


def get_hot_posts(subreddit: str, limit: int = 10) -> Optional[list[dict]]:
    """Fetch hot posts from a subreddit."""
    ensure_on_reddit()
    sr = subreddit.lstrip("r/")
    js = f"""
(function() {{
    fetch("/r/{sr}/hot.json?limit={limit}", {{credentials: "include"}})
        .then(r => r.json())
        .then(d => {{
            var posts = d.data.children.map(p => ({{
                id: p.data.name,
                title: p.data.title,
                score: p.data.score,
                num_comments: p.data.num_comments,
                url: p.data.url,
                selftext: (p.data.selftext || "").substring(0, 200)
            }}));
            document.title = "POSTS:" + JSON.stringify(posts);
        }})
        .catch(e => {{ document.title = "POSTS:ERROR:" + e.message; }});
}})();
"""
    return chrome_js_fetch(js, result_prefix="POSTS:", timeout=15)


def get_new_posts(subreddit: str, limit: int = 15) -> Optional[list[dict]]:
    """Fetch new posts from a subreddit."""
    ensure_on_reddit()
    sr = subreddit.lstrip("r/")
    js = f"""
(function() {{
    fetch("/r/{sr}/new.json?limit={limit}", {{credentials: "include"}})
        .then(r => r.json())
        .then(d => {{
            var posts = d.data.children.map(p => ({{
                id: p.data.name,
                title: p.data.title,
                score: p.data.score,
                num_comments: p.data.num_comments,
                selftext: (p.data.selftext || "").substring(0, 300)
            }}));
            document.title = "POSTS:" + JSON.stringify(posts);
        }})
        .catch(e => {{ document.title = "POSTS:ERROR:" + e.message; }});
}})();
"""
    return chrome_js_fetch(js, result_prefix="POSTS:", timeout=15)


def submit_post(subreddit: str, title: str, text: str, modhash: str) -> Optional[dict]:
    """
    Submit a self (text) post to a subreddit.
    Returns response dict with url on success.
    """
    ensure_on_reddit()
    sr = subreddit.lstrip("r/")
    # Escape for JS string
    title_escaped = title.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    text_escaped = text.replace("\\", "\\\\").replace("`", "\\`")

    js = f"""
(async function() {{
    var body = new URLSearchParams({{
        sr: "{sr}",
        kind: "self",
        title: "{title_escaped}",
        text: `{text_escaped}`,
        uh: "{modhash}",
        api_type: "json",
        resubmit: "true"
    }});
    var resp = await fetch("/api/submit", {{
        method: "POST",
        credentials: "include",
        headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
        body: body.toString()
    }});
    var result = await resp.json();
    document.title = "SUBMITTED:" + JSON.stringify(result);
}})();
"""
    result = chrome_js_fetch(js, result_prefix="SUBMITTED:", timeout=20)
    if result:
        try:
            url = result.get("json", {}).get("data", {}).get("url")
            logger.info("Posted to r/%s: %s", sr, url)
            return {"url": url, "raw": result}
        except Exception:
            pass
    logger.warning("submit_post failed or returned unexpected result")
    return None


def post_comment(thing_id: str, text: str, modhash: str) -> Optional[dict]:
    """
    Post a comment on a post (thing_id = "t3_XXXXX").
    Returns response dict with comment id on success.
    """
    ensure_on_reddit()
    text_escaped = text.replace("\\", "\\\\").replace("`", "\\`")

    js = f"""
(async function() {{
    var body = new URLSearchParams({{
        thing_id: "{thing_id}",
        text: `{text_escaped}`,
        uh: "{modhash}",
        api_type: "json"
    }});
    var resp = await fetch("/api/comment", {{
        method: "POST",
        credentials: "include",
        headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
        body: body.toString()
    }});
    var result = await resp.json();
    document.title = "COMMENTED:" + JSON.stringify(result);
}})();
"""
    return chrome_js_fetch(js, result_prefix="COMMENTED:", timeout=20)


def upvote(post_id: str, modhash: str):
    """Upvote a post (post_id = "t3_XXXXX")."""
    ensure_on_reddit()
    js = f"""
(async function() {{
    var body = new URLSearchParams({{id: "{post_id}", dir: "1", uh: "{modhash}"}});
    await fetch("/api/vote", {{
        method: "POST", credentials: "include",
        headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
        body: body.toString()
    }});
    document.title = "VOTED:ok";
}})();
"""
    chrome_js_fetch(js, result_prefix="VOTED:", timeout=10)


def search_subreddits(keyword: str, limit: int = 10) -> list[dict]:
    """
    Search for subreddits by keyword using Reddit's search API.
    Returns list of {name, title, subscribers, active_user_count, description}.
    Does NOT require login — public JSON endpoint.
    """
    ensure_on_reddit()
    kw = keyword.replace('"', '\\"')
    js = f"""
(function() {{
    fetch("/subreddits/search.json?q={kw}&sort=relevance&limit={limit}", {{credentials: "include"}})
        .then(r => r.json())
        .then(d => {{
            var subs = (d.data.children || []).map(s => ({{
                name: s.data.display_name,
                title: s.data.title,
                subscribers: s.data.subscribers || 0,
                active_user_count: s.data.active_user_count || 0,
                description: (s.data.public_description || "").substring(0, 120)
            }}));
            document.title = "SUBS:" + JSON.stringify(subs);
        }})
        .catch(e => {{ document.title = "SUBS:ERROR:" + e.message; }});
}})();
"""
    result = chrome_js_fetch(js, result_prefix="SUBS:", timeout=15)
    if isinstance(result, list):
        return result
    return []


def get_post_metrics(post_url: str) -> Optional[dict]:
    """Fetch current score and comment count for a post."""
    ensure_on_reddit()
    # Convert to JSON API URL
    api_url = post_url.rstrip("/") + ".json?limit=1"
    js = f"""
(function() {{
    fetch("{api_url}", {{credentials: "include"}})
        .then(r => r.json())
        .then(d => {{
            var post = d[0].data.children[0].data;
            document.title = "METRICS:" + JSON.stringify({{
                score: post.score,
                upvote_ratio: post.upvote_ratio,
                num_comments: post.num_comments,
                title: post.title
            }});
        }})
        .catch(e => {{ document.title = "METRICS:ERROR:" + e.message; }});
}})();
"""
    return chrome_js_fetch(js, result_prefix="METRICS:", timeout=15)
