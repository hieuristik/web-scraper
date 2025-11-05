# src/selenium_driver.py
import sys
import types
import time
import re
from typing import List, Dict
# distutils shim for py3.12 missing distutils
try:
    import distutils  # noqa: F401
except ModuleNotFoundError:
    from packaging.version import parse as _parse
    M = types.ModuleType("distutils")
    Vm = types.ModuleType("distutils.version")
    class LooseVersion:
        def __init__(s,v): s.v=_parse(str(v))
        def _cmp(s,o,op): ov=o.v if isinstance(o,o.__class__) else _parse(str(o)); return op(s.v, ov)
        def __lt__(s,o): return s._cmp(o, lambda a,b:a<b)
        def __le__(s,o): return s._cmp(o, lambda a,b:a<=b)
        def __gt__(s,o): return s._cmp(o, lambda a,b:a>b)
        def __ge__(s,o): return s._cmp(o, lambda a,b:a>=b)
        def __eq__(s,o): return s._cmp(o, lambda a,b:a==b)
        def __ne__(s,o): return s._cmp(o, lambda a,b:a!=b)
    Vm.LooseVersion = LooseVersion
    sys.modules["distutils"]=M
    sys.modules["distutils.version"]=Vm

# Selenium deps
try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys
except Exception as e:
    raise RuntimeError(f"Missing selenium / undetected_chromedriver: {e}")

from .utils import _save_html, _dump, _strip_plus_day_suffix, mmddyyyy
from .parse_structured import parse_cash_flights_structured, parse_award_flights_structured

class AAScraper:
    def __init__(self):
        self.driver = None

    def setup(self):
        print("🚀 Launching Chrome...")
        opts = uc.ChromeOptions()
        opts.add_argument('--disable-blink-features=AutomationControlled')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument("--window-size=1400,1000")
        opts.add_argument("--lang=en-US,en")
        try:
            self.driver = uc.Chrome(options=opts)
            print("✓ Chrome ready")
        except Exception as e:
            raise RuntimeError(f"Failed to start Chrome: {e}")

    def _dismiss_popups(self):
        try:
            self.driver.execute_script("""
                document.querySelectorAll('#onetrust-accept-btn-handler, button[aria-label*="close" i], button[title*="Close" i]').forEach(el => {
                    try { el.click(); } catch(e) {}
                });
                document.querySelectorAll('.onetrust-pc-dark-filter, .onetrust-banner-sdk, adc-cookie-banner').forEach(n=>{ try{ n.remove() }catch(e){}});
            """)
            time.sleep(0.4)
        except:
            pass

    def open_home(self):
        print("🌐 Loading AA.com...")
        self.driver.get("https://www.aa.com/")
        time.sleep(3.0)
        self._dismiss_popups()
        _save_html(self.driver, "home")
        print("✓ Homepage loaded")

    def fill_search_form(self, origin: str, dest: str, date: str, redeem_miles: bool):
        print(f"📝 Filling form ({'Award' if redeem_miles else 'Cash'} mode)...")
        self._dismiss_popups()
        time.sleep(0.3)
        # selection and inputs (kept same as original)
        try:
            self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.oneWay']").click()
            time.sleep(0.22)
        except Exception:
            try:
                ow = self.driver.find_element(By.ID, "flightSearchForm.tripType.oneWay")
                self.driver.execute_script("arguments[0].click()", ow)
                time.sleep(0.22)
            except Exception:
                pass

        # redeem toggle
        try:
            cb = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
            checked = cb.is_selected()
            if redeem_miles and not checked:
                try:
                    lbl = self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.redeemMiles']")
                    lbl.click()
                except:
                    self.driver.execute_script("arguments[0].click()", cb)
                time.sleep(0.24)
            if not redeem_miles and checked:
                try:
                    lbl = self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.redeemMiles']")
                    lbl.click()
                except:
                    self.driver.execute_script("arguments[0].click()", cb)
                time.sleep(0.24)
        except Exception:
            pass

        try:
            origin_input = self.driver.find_element(By.NAME, "originAirport")
            origin_input.clear()
            time.sleep(0.12)
            for ch in origin:
                origin_input.send_keys(ch)
                time.sleep(0.03)
            time.sleep(0.8)
            origin_input.send_keys(Keys.TAB)
            time.sleep(0.4)
        except Exception:
            pass

        try:
            dest_input = self.driver.find_element(By.NAME, "destinationAirport")
            dest_input.clear()
            time.sleep(0.12)
            for ch in dest:
                dest_input.send_keys(ch)
                time.sleep(0.03)
            time.sleep(0.8)
            dest_input.send_keys(Keys.TAB)
            time.sleep(0.4)
        except Exception:
            pass

        try:
            date_val = mmddyyyy(date)
            self.driver.execute_script("""
                const val = arguments[0];
                document.querySelectorAll("input[name*='depart' i]").forEach(inp=>{
                    inp.value = val;
                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                });
            """, date_val)
            time.sleep(0.4)
        except Exception:
            pass

    def submit_search(self):
        self._dismiss_popups()
        try:
            btn = self.driver.find_element(By.XPATH, "//button[contains(., 'Search') or @type='submit']")
            btn.click()
        except Exception:
            try:
                dest = self.driver.find_element(By.NAME, "destinationAirport")
                dest.send_keys(Keys.ENTER)
            except Exception:
                pass
        time.sleep(1.5)

    def _coax_lazy_load(self):
        try:
            for _ in range(3):
                self.driver.execute_script("window.scrollBy(0, window.innerHeight/3);")
                time.sleep(0.35)
            try:
                more_buttons = self.driver.find_elements(By.XPATH, "//button[contains(., 'Show more') or contains(., 'More results') or contains(., 'Load more')]")
                for b in more_buttons:
                    try:
                        if b.is_displayed():
                            self.driver.execute_script("arguments[0].click()", b)
                            time.sleep(0.6)
                    except:
                        continue
            except:
                pass
        except Exception:
            pass

    def wait_for_results(self, timeout=60, is_award: bool = False, expected_min=10) -> bool:
        start = time.time()
        last_count = 0
        while time.time() - start < timeout:
            try:
                counts = self.driver.execute_script("""
                    const modern = document.querySelectorAll('app-slice-details').length;
                    const legacy = document.querySelectorAll('div[class*="result"], div[class*="slice"], div[class*="flight"]').length;
                    return {modern: modern, legacy: legacy};
                """)
                modern = int(counts.get("modern", 0))
                legacy = int(counts.get("legacy", 0))
            except Exception:
                modern = 0
                legacy = 0

            if modern >= expected_min or legacy >= expected_min:
                time.sleep(2.0)
                return True

            try:
                src = self.driver.page_source or ""
            except Exception:
                src = ""

            times_found = len(re.findall(r'(\d{1,2}:\d{2}\s*(?:AM|PM))', src, flags=re.IGNORECASE))
            flights_found = len(re.findall(r'\b([A-Z]{2}\s?\d{1,4})\b', src))
            price_found = len(re.findall(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', src))
            miles_found = len(re.findall(r'(\d+(?:\.\d+)?)\s*K\b', src, flags=re.IGNORECASE)) + len(re.findall(r'(\d{1,3}(?:,\d{3})+)\s*(?:mile|miles|point|points)?', src, flags=re.IGNORECASE))

            if is_award:
                if flights_found >= expected_min and (times_found >= expected_min or miles_found >= expected_min):
                    time.sleep(1.5)
                    return True
            else:
                if flights_found >= expected_min and (times_found >= expected_min or price_found >= expected_min):
                    time.sleep(1.5)
                    return True

            current_count = max(modern, legacy, flights_found)
            if current_count > last_count:
                last_count = current_count
            else:
                self._coax_lazy_load()

            time.sleep(1.0)

        return False

    def parse_flights_structured(self, is_award: bool) -> List[Dict]:
        _save_html(self.driver, f"{'award' if is_award else 'cash'}_results")
        if is_award:
            return parse_award_flights_structured(self.driver)
        else:
            return parse_cash_flights_structured(self.driver)

    def search_flights(self, origin: str, dest: str, date: str, redeem_miles: bool) -> List[Dict]:
        self.fill_search_form(origin, dest, date, redeem_miles)
        self.submit_search()
        ok = self.wait_for_results(timeout=90 if redeem_miles else 60, is_award=redeem_miles, expected_min=10)
        if not ok:
            try:
                self._coax_lazy_load()
                time.sleep(2.0)
                ok = self.wait_for_results(timeout=45, is_award=redeem_miles, expected_min=8)
            except Exception:
                ok = False
        if not ok:
            return []
        return self.parse_flights_structured(is_award=redeem_miles)

    def close(self):
        # Improved shutdown: attempt quit, try to kill process if quit fails, and clear driver reference
        try:
            time.sleep(1.0)
            if self.driver:
                try:
                    self.driver.quit()
                except Exception as e:
                    # Attempt to kill underlying process if available
                    try:
                        svc = getattr(self.driver, "service", None)
                        proc = getattr(svc, "process", None)
                        if proc:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                    except Exception:
                        pass
                finally:
                    # clear reference to avoid destructor calling quit again during interpreter shutdown
                    self.driver = None
        except Exception:
            # best-effort; never raise from close
            self.driver = None
            pass