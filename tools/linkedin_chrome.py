"""
LinkedIn browser tool — JS injection into real Chrome via AppleScript.
Uses LinkedIn's internal Voyager API from within the logged-in session.
"""

import logging
import re
from typing import Optional
from tools.chrome import chrome_js_fetch, chrome_nav, short_delay

logger = logging.getLogger(__name__)

LINKEDIN_HOME = "https://www.linkedin.com/feed/"


def ensure_on_linkedin():
    chrome_nav(LINKEDIN_HOME, wait=3.0)


def _get_csrf() -> Optional[str]:
    """Read LinkedIn's JSESSIONID / csrf token."""
    ensure_on_linkedin()
    js = """
(function() {
    var csrf = document.cookie.split(';')
        .map(c => c.trim())
        .find(c => c.startsWith('JSESSIONID='));
    if (csrf) {
        var val = csrf.split('=')[1].replace(/"/g, '');
        document.title = "CSRF:" + val;
    } else {
        document.title = "CSRF:NOTFOUND";
    }
})();
"""
    result = chrome_js_fetch(js, result_prefix="CSRF:", timeout=10)
    if isinstance(result, str):
        return result if result != "NOTFOUND" else None
    return None


def create_post(text: str) -> Optional[dict]:
    """
    Create a LinkedIn text post via the Voyager API.
    """
    ensure_on_linkedin()
    short_delay(2, 4)

    text_escaped = text.replace("\\", "\\\\").replace("`", "\\`").replace('"', '\\"')

    js = f"""
(async function() {{
    // Get CSRF token
    var csrf = document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('JSESSIONID='));
    if (!csrf) {{ document.title = "LIPOST:ERROR:no csrf"; return; }}
    csrf = csrf.split('=')[1].replace(/"/g, '');

    // Get current user profile ID
    var meResp = await fetch("https://www.linkedin.com/voyager/api/me", {{
        credentials: "include",
        headers: {{
            "csrf-token": csrf,
            "x-restli-protocol-version": "2.0.0"
        }}
    }});
    var me = await meResp.json();
    var miniProfile = me.miniProfile;
    var authorUrn = "urn:li:member:" + me.plainId;

    var payload = {{
        author: authorUrn,
        lifecycleState: "PUBLISHED",
        specificContent: {{
            "com.linkedin.ugc.ShareContent": {{
                shareCommentary: {{
                    text: `{text_escaped}`
                }},
                shareMediaCategory: "NONE"
            }}
        }},
        visibility: {{
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }}
    }};

    var resp = await fetch("https://www.linkedin.com/voyager/api/ugcPosts", {{
        method: "POST",
        credentials: "include",
        headers: {{
            "Content-Type": "application/json",
            "csrf-token": csrf,
            "x-restli-protocol-version": "2.0.0"
        }},
        body: JSON.stringify(payload)
    }});
    var data = await resp.json();
    document.title = "LIPOST:" + JSON.stringify({{status: resp.status, ok: resp.ok, id: data.id || "unknown"}});
}})();
"""
    result = chrome_js_fetch(js, result_prefix="LIPOST:", timeout=25)
    if result and result.get("ok"):
        logger.info("LinkedIn post created: %s", result.get("id"))
        return result
    logger.warning("LinkedIn post may have failed: %s", result)
    return result
