"""
Verify headless Chrome launches correctly with anti-detection flags.
Run on bravos-vm1 to confirm DEPL-05 is satisfied.
"""
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


def get_chrome_options() -> Options:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return opts


def verify_chrome() -> bool:
    # Kill stale Chrome processes
    os.system("pkill -9 -f chrome 2>/dev/null || true")
    os.system("rm -rf /tmp/.org.chromium.* /tmp/chrome_* 2>/dev/null || true")
    time.sleep(1)

    driver = None
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=get_chrome_options(),
        )
        driver.get("about:blank")
        assert "about:blank" in driver.current_url
        print(f"Chrome headless OK — url: {driver.current_url}")
        return True
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    verify_chrome()
