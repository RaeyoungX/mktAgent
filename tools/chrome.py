"""
AppleScript Chrome helpers — same pattern as reddit-cultivate skill.

Python → osascript → real Chrome (already logged in) → platform
No WebDriver, no Playwright, no API tokens. Undetectable.
"""

import json
import logging
import random
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Low-level AppleScript execution ────────────────────────────────────────

def _run_applescript(script: str) -> Optional[str]:
    """Run an AppleScript string, return stdout stripped."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        logger.warning("AppleScript error: %s", result.stderr.strip())
        return None
    return result.stdout.strip()


def _chrome_window_count() -> int:
    out = _run_applescript('tell application "Google Chrome" to return count of windows')
    try:
        return int(out or "0")
    except ValueError:
        return 0


def _detect_method() -> str:
    """Return 'method1' (JS inject) or 'method2' (console paste)."""
    if _chrome_window_count() > 0:
        return "method1"
    # Try to open Chrome
    subprocess.run(["open", "-a", "Google Chrome"], check=False)
    time.sleep(4)
    return "method1" if _chrome_window_count() > 0 else "method2"


# ─── JS execution in Chrome tab ─────────────────────────────────────────────

def chrome_js(js: str, timeout: int = 10) -> Optional[str]:
    """
    Execute JS in Chrome's active tab (Method 1).
    The JS must write its result to document.title for retrieval.
    Returns the document.title value after execution.
    """
    # Escape for AppleScript string embedding
    escaped = js.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    script = f'''
tell application "Google Chrome"
    tell active tab of first window
        execute javascript "{escaped}"
    end tell
end tell
'''
    _run_applescript(script)
    time.sleep(0.6)
    # Read back the title
    return _run_applescript(
        'tell application "Google Chrome" to return title of active tab of first window'
    )


def chrome_js_fetch(js: str, result_prefix: str = "RESULT:", timeout: int = 12) -> Optional[dict]:
    """
    Run async JS that sets document.title = result_prefix + JSON.stringify(...).
    Polls title until prefix appears. Returns parsed JSON dict.
    """
    # Inject the JS
    chrome_js(js)
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = _run_applescript(
            'tell application "Google Chrome" to return title of active tab of first window'
        )
        if title and title.startswith(result_prefix):
            try:
                return json.loads(title[len(result_prefix):])
            except json.JSONDecodeError:
                logger.warning("JSON parse failed for title: %s", title[:200])
                return None
        time.sleep(0.5)
    logger.warning("Timeout waiting for %s in title", result_prefix)
    return None


def chrome_nav(url: str, wait: float = 2.0):
    """Navigate Chrome active tab to a URL and wait."""
    script = f'tell application "Google Chrome" to tell active tab of first window to set URL to "{url}"'
    _run_applescript(script)
    time.sleep(wait)


def chrome_active_url() -> Optional[str]:
    """Return the URL of Chrome's active tab."""
    return _run_applescript(
        'tell application "Google Chrome" to return URL of active tab of first window'
    )


# ─── Method 2: clipboard paste into DevTools console ────────────────────────

def chrome_js_via_console(js: str) -> None:
    """
    Method 2: copy JS to clipboard, open DevTools, paste and run.
    Use when Method 1 fails (JS from Apple Events disabled).
    """
    proc = subprocess.run(["pbcopy"], input=js.encode(), check=True)
    script = '''
tell application "System Events"
    tell process "Google Chrome"
        set frontmost to true
        delay 0.3
        key code 38 using {command down, option down}
        delay 1
        keystroke "a" using {command down}
        delay 0.2
        keystroke "v" using {command down}
        delay 0.5
        key code 36
        delay 0.3
        key code 38 using {command down, option down}
    end tell
end tell'''
    _run_applescript(script)
    time.sleep(2)


# ─── Timing helpers ──────────────────────────────────────────────────────────

def human_delay(min_sec: float = 60, max_sec: float = 180):
    """Sleep for a random human-like duration."""
    t = random.uniform(min_sec, max_sec)
    logger.debug("Waiting %.1fs...", t)
    time.sleep(t)


def short_delay(min_sec: float = 1.5, max_sec: float = 3.5):
    """Short random delay between quick actions."""
    time.sleep(random.uniform(min_sec, max_sec))


# ─── Verification ────────────────────────────────────────────────────────────

def verify_js_from_apple_events() -> bool:
    """Check if Chrome has 'Allow JavaScript from Apple Events' enabled."""
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e",
             'var chrome = Application("Google Chrome"); var tab = chrome.windows[0].activeTab; tab.execute({javascript: "\'ok\'"}); "enabled"'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() == "enabled"
    except Exception:
        return False
