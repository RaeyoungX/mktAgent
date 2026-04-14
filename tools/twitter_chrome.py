"""
Twitter/X browser tool — JS injection into real Chrome via AppleScript.
Uses Twitter's internal web API endpoints from within the logged-in session.
"""

import logging
import re
from typing import Optional
from tools.chrome import chrome_js_fetch, chrome_nav, short_delay

logger = logging.getLogger(__name__)

TWITTER_HOME = "https://twitter.com/home"


def ensure_on_twitter():
    chrome_nav(TWITTER_HOME, wait=2.5)


def _get_ct0() -> Optional[str]:
    """Read the ct0 CSRF token from Twitter cookies."""
    ensure_on_twitter()
    js = """
(function() {
    var ct0 = document.cookie.split(';')
        .map(c => c.trim())
        .find(c => c.startsWith('ct0='));
    document.title = "CT0:" + (ct0 ? ct0.split('=')[1] : 'NOTFOUND');
})();
"""
    result = chrome_js_fetch(js, result_prefix="CT0:", timeout=10)
    if isinstance(result, str):
        return result if result != "NOTFOUND" else None
    return None


def tweet(text: str) -> Optional[dict]:
    """
    Post a tweet using Twitter's internal GraphQL API.
    Returns response dict on success.
    """
    ensure_on_twitter()
    short_delay(1, 2)

    text_escaped = text.replace("\\", "\\\\").replace("`", "\\`").replace('"', '\\"')

    js = f"""
(async function() {{
    var ct0 = document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('ct0='));
    if (!ct0) {{ document.title = "TWEET:ERROR:no ct0"; return; }}
    ct0 = ct0.split('=')[1];

    var payload = {{
        variables: {{
            tweet_text: "{text_escaped}",
            dark_request: false,
            media: {{media_entities: [], possibly_sensitive: false}},
            semantic_annotation_ids: []
        }},
        features: {{
            tweetypie_unmention_optimization_enabled: true,
            responsive_web_edit_tweet_api_enabled: true,
            graphql_is_translatable_rweb_tweet_is_translatable_enabled: true,
            view_counts_everywhere_api_enabled: true,
            longform_notetweets_consumption_enabled: true,
            responsive_web_twitter_article_tweet_consumption_enabled: false,
            tweet_awards_web_tipping_enabled: false,
            longform_notetweets_rich_text_read_enabled: true,
            longform_notetweets_inline_media_enabled: false,
            rweb_video_timestamps_enabled: true,
            responsive_web_graphql_exclude_directive_enabled: true,
            verified_phone_label_enabled: false,
            freedom_of_speech_not_reach_fetch_enabled: true,
            standardized_nudges_misinfo: true,
            tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled: false,
            responsive_web_graphql_skip_user_profile_image_extensions_enabled: false,
            responsive_web_graphql_timeline_navigation_enabled: true,
            responsive_web_enhance_cards_enabled: false
        }},
        queryId: "SoVnbfCycZ7fERGCwpZkYA"
    }};

    var resp = await fetch("https://twitter.com/i/api/graphql/SoVnbfCycZ7fERGCwpZkYA/CreateTweet", {{
        method: "POST",
        credentials: "include",
        headers: {{
            "Content-Type": "application/json",
            "x-csrf-token": ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en"
        }},
        body: JSON.stringify(payload)
    }});
    var data = await resp.json();
    document.title = "TWEET:" + JSON.stringify({{status: resp.status, ok: resp.ok}});
}})();
"""
    result = chrome_js_fetch(js, result_prefix="TWEET:", timeout=20)
    if result and result.get("ok"):
        logger.info("Tweet posted successfully")
        return result
    logger.warning("tweet() may have failed: %s", result)
    return result
