---
name: selenium-scraper
description: >
  General-purpose patterns for Selenium-based web scraping: secure credential
  loading from JSON config, Chrome WebDriver setup with anti-detection, automated
  login with 3-tier click fallback and retry, tab-based dynamic content extraction
  with deduplication, and PostgreSQL writes with row-level error isolation.
  Derived from the optcom-1 scrapers but applicable to any similar gated
  trading/financial data site. Use when asked to explain, build, run, or debug
  a Selenium scraper for a website that requires login and has tabbed data pages.
---

# Selenium Scraper Patterns

These patterns are derived from two production scrapers in the optcom-1 project
(`airflow_project/scripts/options_scraper.py` and `trade_perspectives_scraper.py`)
and can be applied to any similar website.

---

## 1. Credential Loading Pattern

Store credentials in a JSON file (never commit it — add to .gitignore):

```json
{
  "web_scraping": {
    "<service_key>": {
      "username": "user@example.com",
      "password": "secret"
    }
  }
}
```

Load with multi-path fallback:

```python
import json, os
from typing import Optional, Tuple

def load_web_credentials(
    service_key: str,
    config_path: str = None
) -> Tuple[Optional[str], Optional[str]]:
    candidate_paths = [
        config_path,
        '../config/credentials.json',
        '../../config/credentials.json',
        os.path.expanduser('~/project/config/credentials.json'),
    ]
    for path in candidate_paths:
        if path and os.path.exists(path):
            with open(path) as f:
                creds = json.load(f)
            c = creds.get('web_scraping', {}).get(service_key, {})
            username = c.get('username')
            password = c.get('password')
            if username and password:
                return username, password
    return None, None
```

---

## 2. Chrome Driver Setup Pattern

Key concerns: kill stale processes, avoid automation detection, support headless/headed.

```python
import os, time, random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager

def setup_chrome_driver(headless: bool = True):
    # Kill stale Chrome/Chromium processes and temp dirs
    os.system("pkill -9 -f chrome 2>/dev/null || true")
    os.system("pkill -9 -f chromium 2>/dev/null || true")
    os.system("rm -rf /tmp/.org.chromium.* /tmp/chrome_* 2>/dev/null || true")
    time.sleep(2)

    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")

    # Stability flags
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-images")   # faster loads
    options.add_argument("--window-size=1920,1080")

    # Anti-detection (prevents sites recognising Selenium)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # Random debug port avoids conflicts when multiple drivers run
    options.add_argument(f"--remote-debugging-port={random.randint(20000, 60000)}")

    # Retry driver startup up to 3 times
    for attempt in range(3):
        try:
            driver = webdriver.Chrome(
                service=ChromeService(ChromeDriverManager().install()),
                options=options
            )
            driver.get("about:blank")   # verify it loaded
            return driver
        except Exception as e:
            os.system("pkill -9 -f chrome 2>/dev/null || true")
            time.sleep(2)

    return None   # all attempts failed
```

**Debugging tip**: pass `headless=False` to watch the browser; useful when login
or element selectors break.

---

## 3. Login Automation Pattern

Three-tier click strategy (normal → ActionChains → JavaScript) handles overlays,
sticky headers, and React/Angular buttons that intercept normal clicks:

```python
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, ElementClickInterceptedException
)

def automated_login(
    driver,
    login_url: str,
    username: str,
    password: str,
    username_field_name: str = "username",
    password_field_name: str = "password",
    submit_xpath: str = "//button[@type='submit']",
    success_check=None,       # callable(driver) -> bool
    max_retries: int = 3
) -> bool:
    for attempt in range(max_retries):
        try:
            driver.get(login_url)
            time.sleep(2)

            wait = WebDriverWait(driver, 10)
            u = wait.until(EC.presence_of_element_located(
                (By.NAME, username_field_name)))
            p = driver.find_element(By.NAME, password_field_name)

            u.clear(); u.send_keys(username)
            p.clear(); p.send_keys(password)

            btn = wait.until(EC.element_to_be_clickable((By.XPATH, submit_xpath)))
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.5)

            # Tier 1: normal click
            clicked = False
            try:
                btn.click()
                clicked = True
            except ElementClickInterceptedException:
                pass

            # Tier 2: ActionChains (handles hover-required elements)
            if not clicked:
                try:
                    ActionChains(driver).move_to_element(btn).click().perform()
                    clicked = True
                except Exception:
                    pass

            # Tier 3: JavaScript click (always works, bypasses all interception)
            if not clicked:
                driver.execute_script("arguments[0].click();", btn)

            time.sleep(3)

            # Verify success
            if success_check:
                if success_check(driver):
                    return True
            else:
                # Default: login field gone = logged in
                if not driver.find_elements(By.NAME, username_field_name):
                    return True

        except TimeoutException:
            pass

        time.sleep(2)

    return False
```

**Adapting to a new site**: change `username_field_name`, `password_field_name`,
`submit_xpath`, and provide a `success_check` lambda that returns True when
you're on the authenticated page (e.g. check URL, check for logout link).

---

## 4. Tab-Based Data Extraction Pattern

For pages with dynamic tabs (JavaScript-rendered content that changes on click):

```python
import time, random, hashlib
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def extract_tab_data(
    driver,
    url: str,
    tab_selectors: list,      # list of XPath/CSS to try in order
    row_selector: str,        # CSS selector for data rows within active tab
    parse_row_fn,             # callable(element) -> dict
    max_tabs: int = None
) -> list:
    driver.get(url)
    time.sleep(4)

    # Find tabs — try each selector until one works
    tabs = []
    for selector in tab_selectors:
        try:
            found = driver.find_elements(By.XPATH, selector)
            if found:
                tabs = found
                break
        except Exception:
            continue

    if not tabs:
        return []

    results = []
    for tab in tabs[:max_tabs]:
        # Scroll into view + JS click for reliability
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", tab)
        time.sleep(0.5)
        try:
            tab.click()
        except Exception:
            driver.execute_script("arguments[0].click();", tab)

        # Wait for dynamic content to render (3-5 s with jitter)
        time.sleep(random.uniform(3, 5))

        rows = driver.find_elements(By.CSS_SELECTOR, row_selector)
        for row in rows:
            try:
                data = parse_row_fn(row)
                if data:
                    # Hash for dedup — avoids reinserting unchanged rows
                    row_hash = hashlib.md5(str(data).encode()).hexdigest()
                    results.append({**data, "row_hash": row_hash})
            except Exception as e:
                continue   # skip bad rows, keep going

    return results
```

**Expiry date extraction**: after clicking a tab, search the active panel text
with `re.findall(r'\d{4}-\d{2}-\d{2}', panel_text)` and validate dates are
within a reasonable future window (e.g. next 2 years).

---

## 5. Database Write Pattern

Use the project's `DatabaseConnection` context manager; isolate each row so one
bad insert doesn't abort the whole batch:

```python
from database.database_config import DatabaseConnection

def write_rows(rows: list, table: str, unique_col: str = "row_hash"):
    db = DatabaseConnection()
    inserted = 0
    with db.get_connection() as conn:
        cursor = db.get_cursor(conn)
        for row in rows:
            try:
                cols = ", ".join(row.keys())
                placeholders = ", ".join(["%s"] * len(row))
                cursor.execute(
                    f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({unique_col}) DO NOTHING",
                    list(row.values())
                )
                inserted += cursor.rowcount
            except Exception as e:
                # Log and skip — don't abort the batch
                import logging
                logging.warning(f"Row insert failed: {e}")
        conn.commit()
    return inserted
```

---

## 6. Orchestrator Pattern

Putting it together:

```python
def run_scraper(service_key: str, login_url: str, target_urls: list):
    username, password = load_web_credentials(service_key)
    if not username:
        raise RuntimeError("Credentials not found")

    driver = setup_chrome_driver(headless=True)
    if not driver:
        raise RuntimeError("Chrome driver failed to start")

    try:
        logged_in = automated_login(driver, login_url, username, password)
        if not logged_in:
            raise RuntimeError("Login failed")

        all_rows = []
        for url in target_urls:
            rows = extract_tab_data(driver, url, ...)
            all_rows.extend(rows)

        count = write_rows(all_rows, "target_table")
        return count

    finally:
        driver.quit()   # always close the browser
```

---

## 7. Common Failures & Fixes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Login fails all retries | Site layout changed or CAPTCHA added | Run `headless=False`, open DevTools, inspect field names |
| Tabs load but data is empty | Tab index shifted after site update | Print `tab.text` for each tab to find the right index |
| ChromeDriver version mismatch | Chrome browser auto-updated | `pip install -U webdriver-manager` |
| `TimeoutException` on element | Slow page or element renamed | Increase `WebDriverWait` timeout; check selector in headed mode |
| Scraper hangs indefinitely | Chrome zombie from prior crashed run | `pkill -9 -f chrome` then rerun |
| DB insert fails on all rows | Schema mismatch or wrong table name | Print `row.keys()` and compare to actual table columns |
| `ElementClickInterceptedException` | Overlay/cookie banner on top of button | Add a step to dismiss overlays before login, or use JS click directly |

---

## 8. Debugging Workflow

1. Switch to headed mode: `setup_chrome_driver(headless=False)`
2. Add `time.sleep(10)` after navigation to inspect the page
3. Use `driver.page_source` to dump HTML and find correct selectors
4. Use `driver.save_screenshot("debug.png")` to capture visual state
5. For tab content issues: iterate tabs and `print(tab.text, tab.get_attribute('class'))`
