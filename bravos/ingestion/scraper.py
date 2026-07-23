"""
Bravos Trading System — Selenium Scraper.

Persistent Chrome driver (per D-01), session expiry detection (per D-02),
category page scraping (per D-06, D-07), and post body extraction.

Pure ingestion (Session 4b, IMPLEMENTATION_PLAN.md item 8): this module has
no dependency on the order-execution layer and never dispatches an order
directly. The queue-or-dispatch decision (runtime pause check + order
dispatch) lives in scripts/run_ingestion.py::_handle_alert_url, which owns
the routing decision based on the AlertResult this module returns.
"""
import os
import subprocess
import time
import random
import functools
import logging
from dataclasses import dataclass

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)
from urllib3.exceptions import MaxRetryError
from webdriver_manager.chrome import ChromeDriverManager

from bravos.config.secrets_config import get_secret
from bravos.config import settings
from bravos.db import get_connection
from bravos.ingestion.parser import parse_signal

logger = logging.getLogger(__name__)


@dataclass
class AlertResult:
    """
    Structured outcome of process_alert(url) — pure-ingestion contract
    (Session 4b, item 8). Callers (run_ingestion._handle_alert_url) use
    `result` for the socket reply (unchanged from Session 2c: "stored"/
    "duplicate" -> OK, anything else -> ERR) and signal_id/is_new/confidence
    to decide whether to route the signal to execution.
    """
    result: str                    # "stored" | "duplicate" | "failed"
    signal_id: int | None
    is_new: bool
    confidence: str | None


def setup_chrome_driver(headless: bool = True):
    """Create Chrome driver with anti-detection flags. Adapted from selenium-scraper SKILL.md."""
    # WR-05: scope pkill to the current user only to avoid killing unrelated Chrome
    # processes (e.g. admin tools, Puppeteer workers) on a shared GCP VM.
    subprocess.run(
        ["pkill", "-9", "-u", str(os.getuid()), "-f", "chrome.*remote-debugging"],
        check=False,
    )
    time.sleep(1)

    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-images")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_argument(f"--remote-debugging-port={random.randint(20000, 60000)}")

    for attempt in range(3):
        try:
            driver = webdriver.Chrome(
                service=ChromeService(ChromeDriverManager().install()),
                options=options
            )
            driver.get("about:blank")
            return driver
        except Exception as e:
            logger.warning("Chrome driver attempt %d failed: %s", attempt + 1, e)
            # Scope to current user; avoid the broad "chrome" match that can hit
            # unrelated processes (WR-05).
            subprocess.run(
                ["pkill", "-9", "-u", str(os.getuid()), "-f", "chrome"],
                check=False,
            )
            time.sleep(2)
    return None


def catch_cycle_exceptions(job_func):
    """Decorator: catch exceptions in schedule job so daemon does not crash (per research pitfall #3)."""
    @functools.wraps(job_func)
    def wrapper(*args, **kwargs):
        try:
            return job_func(*args, **kwargs)
        except Exception:
            logger.error("Scrape cycle failed — will retry next interval", exc_info=True)
    return wrapper


class BravosScraper:
    """Scrapes bravosresearch.com Trade Alert posts via Selenium."""

    def __init__(self):
        self.driver = None
        self.username = None
        self.password = None

    def startup(self):
        """Create Chrome driver, load credentials from GCP Secret Manager, login (per D-01)."""
        self.username = get_secret("bravos-site-username")
        self.password = get_secret("bravos-site-password")
        self.driver = setup_chrome_driver(headless=True)
        if self.driver is None:
            raise RuntimeError("Failed to start Chrome driver after 3 attempts")
        if not self._login():
            raise RuntimeError("Initial login to bravosresearch.com failed after max attempts")
        logger.info("BravosScraper started — driver active, logged in")

    def _login(self) -> bool:
        """Login with 3-attempt retry (per D-04). Returns True on success.

        Uses By.ID with visible-element filtering because the page has duplicate
        username/password fields (one hidden in nav, one visible in the form).
        """
        for i in range(settings.MAX_REAUTH_ATTEMPTS):
            try:
                self.driver.get(settings.LOGIN_URL)
                time.sleep(2)

                # Find visible username/password fields (page has duplicates; first is hidden)
                username_field = next(
                    (f for f in self.driver.find_elements(By.ID, settings.LOGIN_USERNAME_ID)
                     if f.is_displayed()),
                    None
                )
                password_field = next(
                    (f for f in self.driver.find_elements(By.ID, settings.LOGIN_PASSWORD_ID)
                     if f.is_displayed()),
                    None
                )
                if not username_field or not password_field:
                    logger.warning("Login attempt %d: could not find visible login fields", i + 1)
                    time.sleep(2)
                    continue

                username_field.clear()
                username_field.send_keys(self.username)
                password_field.clear()
                password_field.send_keys(self.password)

                # Click first visible submit button
                btn = next(
                    (b for b in self.driver.find_elements(By.XPATH, settings.LOGIN_SUBMIT_XPATH)
                     if b.is_displayed()),
                    None
                )
                if not btn:
                    logger.warning("Login attempt %d: no visible submit button", i + 1)
                    time.sleep(2)
                    continue

                try:
                    btn.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)

                # Success: no visible login field remains, or URL redirected away from login
                visible_login = [f for f in self.driver.find_elements(By.ID, settings.LOGIN_USERNAME_ID)
                                  if f.is_displayed()]
                if not visible_login:
                    logger.info("Login succeeded on attempt %d", i + 1)
                    return True
                if "wp-login" not in self.driver.current_url and "my-account" not in self.driver.current_url:
                    logger.info("Login succeeded (URL redirect) on attempt %d", i + 1)
                    return True
            except TimeoutException:
                logger.warning("Login attempt %d timed out", i + 1)
            time.sleep(2)
        logger.critical("Login failed after %d attempts", settings.MAX_REAUTH_ATTEMPTS)
        return False

    def _check_session(self) -> bool:
        """Return True if session is valid; False if visible login form detected (per D-02).

        Also returns False (never raises) when the underlying Chrome driver process
        has died — a dead chromedriver raises urllib3 MaxRetryError or a bare
        ConnectionError (e.g. ConnectionRefusedError) instead of WebDriverException,
        since the failure happens at the HTTP transport layer before Selenium can
        wrap it. Callers decide whether to re-login or recreate the driver; this
        method only reports "session not usable".
        """
        try:
            visible_login = [f for f in self.driver.find_elements(By.ID, settings.LOGIN_USERNAME_ID)
                              if f.is_displayed()]
            return not visible_login
        except (WebDriverException, MaxRetryError, ConnectionError) as e:
            logger.error("Session check failed (driver may be dead): %s", e)
            return False

    def fetch_post(self, url: str) -> dict:
        """Navigate to a post URL and return title + body text.

        Called by the Gmail poller with the URL extracted from the notification email.
        Returns dict with keys: title, url, raw_html, text.
        """
        self.driver.get(url)
        try:
            wait = WebDriverWait(self.driver, 15)
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, settings.POST_BODY_SELECTOR)
            ))
        except TimeoutException:
            logger.warning("Timeout waiting for post body at %s", url)
            return {"title": "", "url": url, "raw_html": "", "text": ""}

        # Title: prefer og:title meta (full untruncated string) over article text
        # (article .text.split("\n")[0] truncates when the title wraps in the viewport,
        # causing the ticker symbol to land on line 2 and be silently dropped).
        title = ""
        og_els = self.driver.find_elements(By.CSS_SELECTOR, 'meta[property="og:title"]')
        if og_els:
            title = og_els[0].get_attribute("content") or ""
        if not title:
            article_els = self.driver.find_elements(By.CSS_SELECTOR, "article")
            if article_els:
                title = article_els[0].text.split("\n")[0].strip()

        body_el = self.driver.find_element(By.CSS_SELECTOR, settings.POST_BODY_SELECTOR)
        return {
            "title": title,
            "url": url,
            "raw_html": body_el.get_attribute("innerHTML"),
            "text": body_el.text,
        }

    def _store_signal(self, signal_data: dict) -> tuple[int | None, bool]:
        """Insert signal into DB. ON CONFLICT DO NOTHING for dedup (per D-05, AUDIT-06).

        Returns (signal_id, is_new):
          - is_new=True when a new row was inserted (first time this URL is seen).
          - is_new=False when the post_url already existed (duplicate); signal_id is
            the existing row's id so callers can inspect history, but order
            dispatch must NOT be re-triggered (CR-01: duplicate order guard).

        RETURNING id returns no rows on conflict (see RESEARCH Pitfall 4), so we look
        up the existing id in that case and return is_new=False.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO signals
                      (post_url, post_title, raw_html, ticker, action_type,
                       weight_from, weight_to, reference_price, confidence,
                       parse_method, scraped_at, stop_loss, take_profits)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s)
                    ON CONFLICT (post_url) DO NOTHING
                    RETURNING id
                    """,
                    (
                        signal_data["post_url"], signal_data["post_title"],
                        signal_data["raw_html"], signal_data.get("ticker"),
                        signal_data.get("action_type"), signal_data.get("weight_from"),
                        signal_data.get("weight_to"), signal_data.get("reference_price"),
                        signal_data.get("confidence"), signal_data.get("parse_method"),
                        signal_data.get("stop_loss"),              # float | None -> NUMERIC(10,2)
                        signal_data.get("take_profits", []),       # list[float] -> NUMERIC(10,2)[]; use [] not None (pitfall 3)
                    )
                )
                row = cur.fetchone()
                if row is None:
                    # URL already exists — look up the existing id for logging.
                    # is_new=False: caller must NOT route this signal to order dispatch again
                    # (CR-01: prevents duplicate orders on crash-recovery / scheduler overlap).
                    cur.execute(
                        "SELECT id FROM signals WHERE post_url = %s",
                        (signal_data["post_url"],)
                    )
                    existing = cur.fetchone()
                    conn.commit()
                    return (existing[0] if existing else None, False)
            conn.commit()
        finally:
            conn.close()
        return (row[0] if row else None, True)

    def process_alert(self, url: str) -> AlertResult:
        """Fetch a post URL, parse, and store. Called by the Gmail poller per alert email.

        Ensures session is valid before fetching. Dedup handled by DB ON CONFLICT.

        Pure ingestion (Session 4b, IMPLEMENTATION_PLAN.md item 8): this method
        no longer reads the runtime pause flag, imports the order-execution
        module, dispatches an order, or opens an order-execution DB
        connection. The queue-or-dispatch decision moved to
        run_ingestion._handle_alert_url, which owns the routing decision
        based on the returned AlertResult.

        Contract (originally Session 2c, IMPROVEMENTS.md item 17; extended
        Session 4b): returns an AlertResult(result, signal_id, is_new,
        confidence). `result` is one of "stored", "duplicate", "failed" —
        identical vocabulary to the pre-4b string return, so
        _handle_alert_url's OK/ERR socket-reply mapping is unchanged. Never
        raises — this method owns its own try/except so the truth escapes
        instead of being swallowed.
        """
        try:
            if not self._check_session():
                logger.warning("Session expired — re-authenticating (per D-03)")
                if not self._login():
                    logger.critical("Re-auth failed after %d attempts — skipping alert", settings.MAX_REAUTH_ATTEMPTS)
                    return AlertResult("failed", None, False, None)

            content = self.fetch_post(url)
            parsed = parse_signal(content["title"], content["text"])
            signal_data = {
                "post_url": url,
                "post_title": content["title"],
                "raw_html": content["raw_html"],
                **parsed,
            }
            signal_id, is_new = self._store_signal(signal_data)
            logger.info("Processed: %s -> ticker=%s action=%s confidence=%s signal_id=%s is_new=%s",
                         content["title"][:60], parsed.get("ticker"),
                         parsed.get("action_type"), parsed.get("confidence"), signal_id, is_new)

            if is_new is False:
                # Dedup path — URL already existed in the DB. Not a failure.
                return AlertResult("duplicate", signal_id, False, parsed.get("confidence"))

            if signal_id is None:
                # Edge case: is_new True but no id (unexpected _store_signal
                # oddity). Nothing reliably stored — log and let the poller retry.
                logger.warning(
                    "process_alert: is_new=True but signal_id is None for url=%s — treating as failed",
                    url,
                )
                return AlertResult("failed", None, False, None)

            # Phase 7: parse failure spike tracking (D-03, NOTF-02). Duplicates
            # are not counted — only genuinely parsed new signals reach here.
            try:
                from bravos.notifications.notifier import record_parse_outcome
                record_parse_outcome(parsed)
            except Exception:
                logger.warning("record_parse_outcome failed — continuing", exc_info=True)

            return AlertResult("stored", signal_id, True, parsed.get("confidence"))
        except Exception:
            logger.exception("process_alert failed for url=%s", url)
            return AlertResult("failed", None, False, None)

    def shutdown(self):
        """Graceful shutdown — quit Chrome driver (SIGTERM handler)."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Chrome driver closed")
            except Exception:
                logger.warning("Error closing Chrome driver", exc_info=True)
