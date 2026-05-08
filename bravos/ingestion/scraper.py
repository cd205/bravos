"""
Bravos Trading System — Selenium Scraper.

Persistent Chrome driver (per D-01), session expiry detection (per D-02),
category page scraping (per D-06, D-07), and post body extraction.
"""
import os
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
    os.system("pkill -9 -f 'chrome.*remote-debugging' 2>/dev/null || true")
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
            os.system("pkill -9 -f chrome 2>/dev/null || true")
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

    def _login(self, attempt: int = 0) -> bool:
        """Login with 3-attempt retry (per D-04). Returns True on success."""
        for i in range(settings.MAX_REAUTH_ATTEMPTS):
            try:
                self.driver.get(settings.LOGIN_URL)
                time.sleep(2)
                wait = WebDriverWait(self.driver, 10)
                username_field = wait.until(
                    EC.presence_of_element_located((By.NAME, settings.LOGIN_USERNAME_FIELD))
                )
                password_field = self.driver.find_element(By.NAME, settings.LOGIN_PASSWORD_FIELD)
                username_field.clear()
                username_field.send_keys(self.username)
                password_field.clear()
                password_field.send_keys(self.password)

                btn = wait.until(EC.element_to_be_clickable((By.XPATH, settings.LOGIN_SUBMIT_XPATH)))
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.5)
                try:
                    btn.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)

                # Success check: login field gone
                if not self.driver.find_elements(By.NAME, settings.LOGIN_USERNAME_FIELD):
                    logger.info("Login succeeded on attempt %d", i + 1)
                    return True
                # Also check URL — may have redirected away from login
                if "wp-login" not in self.driver.current_url and "my-account" not in self.driver.current_url:
                    logger.info("Login succeeded (URL redirect) on attempt %d", i + 1)
                    return True
            except TimeoutException:
                logger.warning("Login attempt %d timed out", i + 1)
            time.sleep(2)
        logger.critical("Login failed after %d attempts", settings.MAX_REAUTH_ATTEMPTS)
        return False

    def _check_session(self) -> bool:
        """Return True if session is valid; False if login form detected (per D-02)."""
        try:
            login_forms = self.driver.find_elements(By.NAME, settings.LOGIN_USERNAME_FIELD)
            if login_forms:
                return False
            if "wp-login" in self.driver.current_url or "my-account" in self.driver.current_url:
                # Check if we were redirected to login page
                login_forms = self.driver.find_elements(By.NAME, settings.LOGIN_USERNAME_FIELD)
                if login_forms:
                    return False
            return True
        except WebDriverException:
            logger.error("WebDriverException during session check — driver may have crashed")
            return False

    def _get_recent_posts(self, limit: int = None) -> list:
        """Scrape the category page for recent Trade Alert posts (per D-06, D-07).

        Returns list of dicts with keys: title, url.
        """
        if limit is None:
            limit = settings.POSTS_PER_CYCLE
        try:
            self.driver.get(settings.TRADE_ALERT_CATEGORY_URL)
            wait = WebDriverWait(self.driver, 15)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "article")))
        except TimeoutException:
            logger.warning("Timeout waiting for articles on category page")
            return []

        articles = self.driver.find_elements(By.CSS_SELECTOR, settings.ARTICLE_SELECTOR)[:limit]
        posts = []
        for article in articles:
            try:
                link = article.find_element(By.CSS_SELECTOR, settings.ARTICLE_LINK_SELECTOR)
                posts.append({
                    "title": link.text.strip(),
                    "url": link.get_attribute("href"),
                })
            except NoSuchElementException:
                continue
        logger.info("Found %d posts on category page", len(posts))
        return posts

    def _get_post_content(self, url: str) -> dict:
        """Navigate to a post URL and extract the full body HTML."""
        self.driver.get(url)
        try:
            wait = WebDriverWait(self.driver, 15)
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, settings.POST_BODY_SELECTOR)
            ))
            body_el = self.driver.find_element(By.CSS_SELECTOR, settings.POST_BODY_SELECTOR)
            return {
                "raw_html": body_el.get_attribute("innerHTML"),
                "text": body_el.text,
            }
        except (TimeoutException, NoSuchElementException):
            logger.warning("Could not extract body from %s", url)
            return {"raw_html": "", "text": ""}

    def _store_signal(self, signal_data: dict):
        """Insert signal into DB. ON CONFLICT DO NOTHING for dedup (per D-05, AUDIT-06)."""
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
                       parse_method, scraped_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (post_url) DO NOTHING
                    """,
                    (
                        signal_data["post_url"], signal_data["post_title"],
                        signal_data["raw_html"], signal_data.get("ticker"),
                        signal_data.get("action_type"), signal_data.get("weight_from"),
                        signal_data.get("weight_to"), signal_data.get("reference_price"),
                        signal_data.get("confidence"), signal_data.get("parse_method"),
                    )
                )
            conn.commit()
        finally:
            conn.close()

    @catch_cycle_exceptions
    def run_cycle(self):
        """Single 5-minute cycle: check session, scrape, parse, store (per D-13)."""
        if not self._check_session():
            logger.warning("Session expired — re-authenticating (per D-03)")
            if not self._login():
                logger.critical("Re-auth failed after %d attempts — skipping cycle (per D-04)", settings.MAX_REAUTH_ATTEMPTS)
                return

        posts = self._get_recent_posts()
        if not posts:
            logger.warning("0 posts returned — site unreachable or empty (per D-08)")
            return

        for post in posts:
            self._process_post(post)

    def _process_post(self, post: dict):
        """Fetch body, parse, store. Dedup handled by DB constraint."""
        content = self._get_post_content(post["url"])
        parsed = parse_signal(post["title"], content["text"])
        signal_data = {
            "post_url": post["url"],
            "post_title": post["title"],
            "raw_html": content["raw_html"],
            **parsed,
        }
        self._store_signal(signal_data)
        logger.info("Processed: %s -> ticker=%s action=%s confidence=%s",
                     post["title"][:60], parsed.get("ticker"),
                     parsed.get("action_type"), parsed.get("confidence"))

    def shutdown(self):
        """Graceful shutdown — quit Chrome driver (SIGTERM handler)."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Chrome driver closed")
            except Exception:
                logger.warning("Error closing Chrome driver", exc_info=True)
