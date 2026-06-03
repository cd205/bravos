"""
Bravos Trading System — Selenium Scraper.

Persistent Chrome driver (per D-01), session expiry detection (per D-02),
category page scraping (per D-06, D-07), and post body extraction.
"""
import os
import subprocess
import time
import random
import functools
import logging

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)
from webdriver_manager.chrome import ChromeDriverManager

from bravos.config.secrets_config import get_secret
from bravos.config import settings
from bravos.ingestion.parser import parse_signal

logger = logging.getLogger(__name__)


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
        """Return True if session is valid; False if visible login form detected (per D-02)."""
        try:
            visible_login = [f for f in self.driver.find_elements(By.ID, settings.LOGIN_USERNAME_ID)
                              if f.is_displayed()]
            return not visible_login
        except WebDriverException:
            logger.error("WebDriverException during session check — driver may have crashed")
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
            the existing row's id so callers can inspect history, but execute_signal
            must NOT be re-triggered (CR-01: duplicate order guard).

        RETURNING id returns no rows on conflict (see RESEARCH Pitfall 4), so we look
        up the existing id in that case and return is_new=False.
        """
        import psycopg2
        password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=password,
        )
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
                    # is_new=False: process_alert must NOT call execute_signal again
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

    @catch_cycle_exceptions
    def process_alert(self, url: str):
        """Fetch a post URL, parse, and store. Called by the Gmail poller per alert email.

        Ensures session is valid before fetching. Dedup handled by DB ON CONFLICT.
        """
        if not self._check_session():
            logger.warning("Session expired — re-authenticating (per D-03)")
            if not self._login():
                logger.critical("Re-auth failed after %d attempts — skipping alert", settings.MAX_REAUTH_ATTEMPTS)
                return

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

        # Phase 7: parse failure spike tracking (D-03, NOTF-02). Duplicates
        # (signal_id is None) are not counted — only genuinely parsed signals.
        if signal_id is not None:
            try:
                from bravos.notifications.notifier import record_parse_outcome
                record_parse_outcome(parsed)
            except Exception:
                logger.warning("record_parse_outcome failed — continuing", exc_info=True)

        # Phase 4 D-12: route stored signal to the order path.
        # CR-01: Only route NEW inserts to execute_signal. is_new=False means this
        # URL was already in the DB (duplicate from crash-recovery, scheduler overlap,
        # or manual retry). Re-routing a duplicate would place a second order for the
        # same signal — no uniqueness constraint on (signal_id) in orders table prevents it.
        # signal_id may still be None if the DB lookup itself failed (is_new=False case).
        if is_new and signal_id is not None and parsed.get("confidence") == "high":
            from bravos.execution.executor import execute_signal
            import psycopg2
            password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
            exec_conn = psycopg2.connect(
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                dbname=settings.DB_NAME,
                user=settings.DB_USER,
                password=password,
            )
            try:
                execute_signal(signal_id, exec_conn)
            except Exception:
                logger.exception("execute_signal raised for signal_id=%s — continuing", signal_id)
            finally:
                exec_conn.close()

    def shutdown(self):
        """Graceful shutdown — quit Chrome driver (SIGTERM handler)."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Chrome driver closed")
            except Exception:
                logger.warning("Error closing Chrome driver", exc_info=True)
