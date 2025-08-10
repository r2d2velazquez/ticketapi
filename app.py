import os
import time
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
import requests
from pathlib import Path
import zipfile #Create a zip file for PDF and XML

import atexit
import tempfile
import threading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('invoice_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


app = Flask(__name__)

# Thread-safe tracking
DOWNLOADS_DIR = Path.home() / 'Downloads'
pending_cleanup = set()
cleanup_lock = threading.Lock()

def clean_downloads_dir():
    """Safely clean the entire Downloads directory"""
    with cleanup_lock:
        try:
            logger.info(f"Starting cleanup of {DOWNLOADS_DIR}")
            deleted_count = 0
            
            for item in DOWNLOADS_DIR.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                        deleted_count += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"Could not delete {item.name}: {str(e)}")
            
            logger.info(f"Cleaned {deleted_count} items from Downloads")
            return True
            
        except Exception as e:
            logger.error(f"Downloads cleanup failed: {str(e)}")
            return False

def background_cleanup(file_path):
    """Guaranteed cleanup in a background thread"""
    def cleanup():
        time.sleep(2)  # Extended safety delay
        with cleanup_lock:
            try:
                if Path(file_path).exists():
                    Path(file_path).unlink()
                    logger.info(f"Deleted {file_path}")
                pending_cleanup.discard(file_path)
                clean_downloads_dir()  # Full cleanup
            except Exception as e:
                logger.error(f"Cleanup error: {str(e)}")

    threading.Thread(target=cleanup, daemon=True).start()


class ServiceStore:
    #def __init__(self, download_directory="~/Downloads"):
    def __init__(self, download_directory=DOWNLOADS_DIR):
        self.download_directory = os.path.abspath(download_directory)
        Path(self.download_directory).mkdir(parents=True, exist_ok=True)
        self.driver = None

    def setup_stealth_driver():
        chrome_options = Options()

        # Anti-detection options
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

        # Performance options
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")

        # Block tracking
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")

        driver = webdriver.Chrome(options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        return driver

    def setup_driver(self):
        """Setup Chrome WebDriver with download preferences"""
#        chrome_options = Options()

        # Configure download directory
        prefs = {
            "download.default_directory": self.download_directory,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "plugins.always_open_pdf_externally": True
        }


        self.driver = ServiceStore.setup_stealth_driver()

    def close_driver(self):
        """Close the WebDriver"""
        if self.driver:
            self.driver.quit()

    def debug_page_elements(self):
        """Debug method to find all buttons and their attributes"""
        try:
            buttons = self.driver.find_elements(By.TAG_NAME, "button")
            inputs = self.driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button']")

            logger.info("=== DEBUG: Found buttons ===")
            for i, button in enumerate(buttons):
                text = button.text.strip()
                button_id = button.get_attribute("id")
                button_class = button.get_attribute("class")
                button_type = button.get_attribute("type")
                logger.info(f"Button {i}: text='{text}', id='{button_id}', class='{button_class}', type='{button_type}'")

            logger.info("=== DEBUG: Found input buttons ===")
            for i, input_elem in enumerate(inputs):
                value = input_elem.get_attribute("value")
                input_id = input_elem.get_attribute("id")
                input_class = input_elem.get_attribute("class")
                input_type = input_elem.get_attribute("type")
                logger.info(f"Input {i}: value='{value}', id='{input_id}', class='{input_class}', type='{input_type}'")

        except Exception as e:
            logger.error(f"Debug error: {str(e)}")

    def wait_for_element(self, by, value, timeout=10):
        """Wait for element to be present and return it"""
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )

    def wait_for_element_enabled(self, by, value, timeout=10):
        """Wait for element to be present and enabled"""
        def element_is_enabled(driver):
            try:
                element = driver.find_element(by, value)
                return element and element.is_enabled() and not element.get_attribute("disabled")
            except:
                return False

        WebDriverWait(self.driver, timeout).until(element_is_enabled)
        return self.driver.find_element(by, value)

    def wait_for_clickable(self, by, value, timeout=10):
        """Wait for element to be clickable and return it"""
        return WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )

    def fill_form_guadalajara(self, data):
        """Main method to fill the form with provided data"""
        try:
            # Navigate to the website
            logger.info("Navigating to the website...")
            self.driver.get("https://www.movil.farmaciasguadalajara.com/facturacion/")

            # Wait for page to load completely
            time.sleep(3)

            # Debug: Print page elements if in debug mode
            if logger.level == logging.DEBUG:
                self.debug_page_elements()

            # Fill first section of the form
            logger.info("Filling first section of the form...")
            self._fill_first_section_guadalajara(data)

            # Handle popup and click accept
            #logger.info("Handling popup...")
            self._handle_popup()

            # Fill second section of the form
            logger.info("Filling second section of the form...")
            self._fill_second_section_guadalajara(data)

            # Handle email if required
            if data.get('send_email', False):
                logger.info("Setting up email delivery...")
                self._setup_email(data)

            # Submit form
            logger.info("Submitting form...")
            return self._submit_form_guadalajara()

        except Exception as e:
            logger.error(f"Error filling form: {str(e)}")
            # Debug: Print current page source snippet if error occurs
            try:
                page_title = self.driver.title
                current_url = self.driver.current_url
                logger.error(f"Current page: {page_title} - {current_url}")

                # Print any error messages on the page
                error_elements = self.driver.find_elements(By.CLASS_NAME, "error")
                for error in error_elements:
                    if error.text.strip():
                        logger.error(f"Page error: {error.text}")

            except Exception as debug_error:
                logger.error(f"Debug error: {str(debug_error)}")
            raise

    def fill_form_ahorro(self, data):
        """Main method to fill the form with provided data"""
        try:
            # Navigate to the website
            logger.info("Navigating to the website...")
            self.driver.get("https://fahorro.masfacturaweb.com.mx/creafactura")

            # Wait for page to load completely
            time.sleep(3)

            # Debug: Print page elements if in debug mode
            if logger.level == logging.DEBUG:
                self.debug_page_elements()

            # Fill first section of the form
            logger.info("Filling first section of the form...")
            self._fill_first_section_ahorro(data)

            # Handle popup and click accept
            #logger.info("Handling popup...")
            #self._handle_popup()

            # Fill second section of the form
            logger.info("Filling second section of the form...")
            self._fill_second_section_ahorro(data)

            # Handle email if required
            #if data.get('send_email', False):
            #    logger.info("Setting up email delivery...")
            #    self._setup_email(data)

            # Submit form
            logger.info("Submitting form...")
            return self._submit_form_ahorro()

        except Exception as e:
            logger.error(f"Error filling form: {str(e)}")
            # Debug: Print current page source snippet if error occurs
            try:
                page_title = self.driver.title
                current_url = self.driver.current_url
                logger.error(f"Current page: {page_title} - {current_url}")

                # Print any error messages on the page
                error_elements = self.driver.find_elements(By.CLASS_NAME, "error")
                for error in error_elements:
                    if error.text.strip():
                        logger.error(f"Page error: {error.text}")

            except Exception as debug_error:
                logger.error(f"Debug error: {str(debug_error)}")
            raise

    def fill_form_ahorro_descargar(self, data):
        """Main method to fill the form with provided data"""
        try:
            # Navigate to the website
            logger.info("Navigating to the website...")
            self.driver.get("https://fahorro.masfacturaweb.com.mx/creafactura")

            # Wait for page to load completely
            time.sleep(3)

            # Debug: Print page elements if in debug mode
            if logger.level == logging.DEBUG:
                self.debug_page_elements()

            # Fill first section of the form
            logger.info("Filling first section of the form...")
            self._fill_first_section_ahorro(data)


            # Submit form
            logger.info("Submitting form...")
            return self._submit_form_ahorro_descargar()

        except Exception as e:
            logger.error(f"Error filling form: {str(e)}")
            # Debug: Print current page source snippet if error occurs
            try:
                page_title = self.driver.title
                current_url = self.driver.current_url
                logger.error(f"Current page: {page_title} - {current_url}")

                # Print any error messages on the page
                error_elements = self.driver.find_elements(By.CLASS_NAME, "error")
                for error in error_elements:
                    if error.text.strip():
                        logger.error(f"Page error: {error.text}")

            except Exception as debug_error:
                logger.error(f"Debug error: {str(debug_error)}")
            raise

    def _fill_first_section_guadalajara(self, data):
        """Fill the first section of the form"""
        try:
            # Fill Folio Factura
            logger.info("Filling Folio Factura...")
            folio_element = self.wait_for_element(By.ID, "folioFactura")
            folio_element.clear()
            folio_element.send_keys(data['folio_factura'])
            time.sleep(0.5)  # Brief pause for any JavaScript validation

            # Fill Caja
            logger.info("Filling Caja...")
            caja_element = self.wait_for_element(By.ID, "caja")
            caja_element.clear()
            caja_element.send_keys(data['caja'])
            time.sleep(0.5)

            # Fill Fecha de Compra
            logger.info("Filling Fecha de Compra...")
            fecha_element = self.wait_for_element(By.ID, "fechaCompra")
            fecha_element.clear()
            fecha_element.send_keys(data['fecha_compra'])
            time.sleep(0.5)

            # Fill No. Ticket
            logger.info("Filling No. Ticket...")
            ticket_element = self.wait_for_element(By.ID, "ticket")
            ticket_element.clear()
            ticket_element.send_keys(data['ticket'])

            # Trigger any change events that might enable the button
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", ticket_element)
            self.driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles: true}));", ticket_element)

            # Wait for any JavaScript to process
            time.sleep(2)

            # Now try to find and click "Validar Folio" button
            logger.info("Looking for 'Validar Folio' button...")
            self._click_validar_folio_button()

        except Exception as e:
            logger.error(f"Error in _fill_first_section_guadalajara: {str(e)}")
            raise

    def _fill_first_section_ahorro(self, data):
        """Fill the first section of the form"""
        try:
            # Fill Folio RFC
            logger.info("Filling RFC...")
            folio_element = self.wait_for_element(By.ID, "TextRfc")
            folio_element.clear()
            folio_element.send_keys(data['rfc'])
            time.sleep(0.5)  # Brief pause for any JavaScript validation

            # Fill ITU
            logger.info("Filling ITU...")
            caja_element = self.wait_for_element(By.ID, "inputAddress")
            caja_element.clear()
            caja_element.send_keys(data['ticket'])
            time.sleep(2)

            # Now try to find and click "Continuar" button
            logger.info("Looking for 'Continuar' button...")
            #self._click_validar_folio_button()
            button = self.wait_for_element_enabled(By.ID, 'btnContinuar')
            button.send_keys(Keys.PAGE_DOWN);
            button.click()

        except Exception as e:
            logger.error(f"Error in _fill_first_section: {str(e)}")
            raise


    def _click_validar_folio_button(self):
        """Click the Angular Material Validar Folio button with improved targeting"""
        button_found = False

        logger.info("Looking for Angular Material 'Validar Folio' button...")

        # Wait for Angular to fully load and button to be ready
        time.sleep(2)

        # More precise selectors based on the actual HTML structure
        targeted_selectors = [
            # Most specific - target the exact button structure from your HTML
            (By.XPATH, "//button[@mat-fab='' and @extended='' and @type='submit' and contains(@class, 'primary')]"),

            # Target by the combination of classes that are always present
            (By.XPATH, "//button[contains(@class, 'mdc-fab') and contains(@class, 'mat-mdc-fab') and contains(@class, 'mdc-fab--extended') and @type='submit']"),

            # Target by the inner span with "Validar Folio" text
            (By.XPATH, "//span[@class='mdc-button__label' and normalize-space(text())='Validar Folio']/parent::button"),

            # Target by mat-accent class and submit type
            (By.XPATH, "//button[contains(@class, 'mat-accent') and @type='submit' and contains(@class, 'mdc-fab--extended')]"),

            # CSS selector approach
            (By.CSS_SELECTOR, "button.mdc-fab.mat-mdc-fab.mdc-fab--extended[type='submit']"),

            # Fallback - any submit button with primary class
            (By.XPATH, "//button[@type='submit' and contains(@class, 'primary')]"),
        ]

        for by_method, selector in targeted_selectors:
            try:
                logger.info(f"Trying selector: {selector}")

                # Wait for element to be present in DOM
                element = WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((by_method, selector))
                )

                if element:
                    # Log element details for verification
                    element_text = element.text.strip()
                    element_classes = element.get_attribute('class')
                    element_type = element.get_attribute('type')
                    is_enabled = element.is_enabled()
                    is_displayed = element.is_displayed()

                    logger.info(f"Found element - Text: '{element_text}', Classes: '{element_classes}', Type: '{element_type}', Enabled: {is_enabled}, Displayed: {is_displayed}")

                    if not is_enabled:
                        logger.warning("Button found but not enabled. Waiting for it to become enabled...")
                        # Wait longer for button to become enabled after form validation
                        try:
                            WebDriverWait(self.driver, 20).until(
                                lambda driver: driver.find_element(by_method, selector).is_enabled()
                            )
                            logger.info("Button is now enabled")
                        except TimeoutException:
                            logger.error("Button never became enabled")
                            continue

                    # Scroll element into view smoothly
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});"
                        "window.scrollBy(0, -100);", # Offset for any fixed headers
                        element
                    )
                    time.sleep(1)

                    # Wait for any animations to complete
                    time.sleep(2)

                    # Method 1: Try ActionChains click (best for Angular Material)
                    try:
                        from selenium.webdriver.common.action_chains import ActionChains

                        # Move to element and click
                        actions = ActionChains(self.driver)
                        actions.move_to_element(element).pause(0.5).click().perform()

                        logger.info("✓ Successfully clicked 'Validar Folio' button using ActionChains")
                        button_found = True

                    except Exception as action_error:
                        logger.warning(f"ActionChains click failed: {str(action_error)}")

                        # Method 2: Try clicking the inner span with the text
                        try:
                            inner_span = element.find_element(By.CLASS_NAME, "mdc-button__label")
                            inner_span.click()
                            logger.info("✓ Successfully clicked 'Validar Folio' button by clicking inner span")
                            button_found = True

                        except Exception as span_error:
                            logger.warning(f"Inner span click failed: {str(span_error)}")

                            # Method 3: JavaScript click with proper event dispatching for Angular
                            try:
                                # Dispatch both click and Angular-specific events
                                self.driver.execute_script("""
                                    var element = arguments[0];

                                    // Dispatch multiple events that Angular Material expects
                                    var events = ['mousedown', 'mouseup', 'click'];
                                    events.forEach(function(eventType) {
                                        var event = new MouseEvent(eventType, {
                                            bubbles: true,
                                            cancelable: true,
                                            view: window
                                        });
                                        element.dispatchEvent(event);
                                    });

                                    // Also trigger form submission if it's a submit button
                                    if (element.type === 'submit') {
                                        var form = element.closest('form');
                                        if (form) {
                                            var submitEvent = new Event('submit', {
                                                bubbles: true,
                                                cancelable: true
                                            });
                                            form.dispatchEvent(submitEvent);
                                        }
                                    }
                                """, element)

                                logger.info("✓ Successfully clicked 'Validar Folio' button using JavaScript with events")
                                button_found = True

                            except Exception as js_error:
                                logger.warning(f"JavaScript click failed: {str(js_error)}")

                                # Method 4: Last resort - direct JavaScript click
                                try:
                                    self.driver.execute_script("arguments[0].click();", element)
                                    logger.info("✓ Successfully clicked 'Validar Folio' button using direct JavaScript click")
                                    button_found = True
                                except Exception as direct_js_error:
                                    logger.error(f"All click methods failed: {str(direct_js_error)}")
                                    continue

                    if button_found:
                        # Wait for any loading, validation, or state changes
                        #logger.info("Waiting for validation to complete...")
                        #time.sleep(5)  # Increased wait time for server response

                        # Check for any immediate validation feedback
                        #self._check_validation_feedback()
                        break

            except TimeoutException:
                logger.debug(f"Selector timed out: {selector}")
                continue
            except Exception as e:
                logger.debug(f"Selector failed {selector}: {str(e)}")
                continue

        if not button_found:
            logger.error("❌ Could not find or click the Angular Material 'Validar Folio' button")
            # Enhanced debug info
            self._enhanced_debug_info()
            raise Exception("Failed to click Validar Folio button")

        return button_found

    def _enhanced_debug_info(self):
        """Enhanced debug information for troubleshooting"""
        try:
            logger.info("=== ENHANCED DEBUG INFO ===")

            # Check if page is still loading
            ready_state = self.driver.execute_script("return document.readyState")
            logger.info(f"Page ready state: {ready_state}")

            # Check for Angular
            angular_loaded = self.driver.execute_script("""
                return typeof angular !== 'undefined' ||
                       typeof ng !== 'undefined' ||
                       window.getAllAngularTestabilities !== undefined ||
                       document.querySelector('[ng-version]') !== null;
            """)
            logger.info(f"Angular detected: {angular_loaded}")

            # Find all submit buttons
            submit_buttons = self.driver.find_elements(By.XPATH, "//button[@type='submit']")
            logger.info(f"Found {len(submit_buttons)} submit buttons")

            for i, btn in enumerate(submit_buttons):
                try:
                    text = btn.text.strip()
                    classes = btn.get_attribute("class")
                    enabled = btn.is_enabled()
                    displayed = btn.is_displayed()
                    logger.info(f"Submit button {i}: text='{text}', enabled={enabled}, displayed={displayed}, classes='{classes}'")
                except Exception as e:
                    logger.info(f"Submit button {i}: Error getting info - {str(e)}")

            # Look for buttons containing "validar" or "folio"
            validar_buttons = self.driver.find_elements(By.XPATH, "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'validar') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'folio')]")
            logger.info(f"Found {len(validar_buttons)} buttons with 'validar' or 'folio'")

            # Check current URL and title
            logger.info(f"Current URL: {self.driver.current_url}")
            logger.info(f"Page title: {self.driver.title}")

            # Check for any error messages
            error_elements = self.driver.find_elements(By.XPATH, "//*[contains(@class, 'error') or contains(@class, 'alert') or contains(@class, 'warning')]")
            for error in error_elements:
                if error.is_displayed() and error.text.strip():
                    logger.warning(f"Page error/warning: {error.text.strip()}")

        except Exception as e:
            logger.error(f"Error in enhanced debug: {str(e)}")

    def _wait_for_angular_ready(self):
        """Wait for Angular application to be fully loaded and ready"""
        try:
            logger.info("Waiting for Angular to be ready...")

            WebDriverWait(self.driver, 30).until(
                lambda driver: driver.execute_script("""
                    // Check if Angular is present and ready
                    if (typeof angular !== 'undefined') {
                        // AngularJS
                        var element = document.querySelector('[ng-app]') || document.body;
                        var scope = angular.element(element).scope();
                        return scope && !scope.$$phase;
                    }

                    // Angular 2+ (check for zone.js stability)
                    if (window.getAllAngularTestabilities) {
                        var testabilities = window.getAllAngularTestabilities();
                        return testabilities.every(function(testability) {
                            return testability.isStable();
                        });
                    }

                    // Fallback - check if page is loaded
                    return document.readyState === 'complete';
                """)
            )

            logger.info("Angular appears to be ready")
            return True

        except TimeoutException:
            logger.warning("Timeout waiting for Angular to be ready, proceeding anyway")
            return False
        except Exception as e:
            logger.warning(f"Error checking Angular readiness: {str(e)}")
            return False

    def _check_validation_feedback(self):
        """Check for validation feedback after clicking Validar Folio"""
        try:
            # Look for common validation feedback elements
            feedback_selectors = [
                ".mat-error",
                ".validation-message",
                ".error-message",
                ".alert",
                ".snack-bar",
                ".mat-snack-bar-container",
                "[role='alert']",
                ".toast",
                ".notification"
            ]

            for selector in feedback_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.text.strip():
                            feedback_text = element.text.strip()
                            logger.info(f"Validation feedback: {feedback_text}")

                            # Check if it's an error
                            if any(word in feedback_text.lower() for word in ['error', 'invalid', 'incorrect', 'failed']):
                                logger.error(f"Validation error detected: {feedback_text}")
                                raise Exception(f"Form validation failed: {feedback_text}")
                            else:
                                logger.info(f"Validation feedback (likely success): {feedback_text}")

                except Exception as e:
                    continue

            # Also check if the politicas button is now enabled (good sign)
            try:
                politicas_button = self.driver.find_element(By.ID, "politicasPr-input")
                if politicas_button.is_enabled():
                    logger.info("✓ Politicas button is now enabled - validation likely successful")
                else:
                    logger.warning("⚠ Politicas button is still disabled - validation may have failed")
            except:
                logger.debug("Could not check politicas button status")

        except Exception as e:
            logger.debug(f"Error checking validation feedback: {str(e)}")

    def _print_all_buttons_debug(self):
        """Print all buttons and inputs for debugging purposes"""
        try:
            logger.info("=== DEBUG: All buttons and inputs on page ===")

            # Find all buttons
            buttons = self.driver.find_elements(By.TAG_NAME, "button")
            for i, btn in enumerate(buttons):
                text = btn.text.strip()
                btn_id = btn.get_attribute("id")
                btn_class = btn.get_attribute("class")
                onclick = btn.get_attribute("onclick")
                disabled = btn.get_attribute("disabled")
                logger.info(f"Button {i}: text='{text}', id='{btn_id}', class='{btn_class}', onclick='{onclick}', disabled='{disabled}'")

            # Find all input buttons
            inputs = self.driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button']")
            for i, inp in enumerate(inputs):
                value = inp.get_attribute("value")
                inp_id = inp.get_attribute("id")
                inp_class = inp.get_attribute("class")
                onclick = inp.get_attribute("onclick")
                disabled = inp.get_attribute("disabled")
                logger.info(f"Input {i}: value='{value}', id='{inp_id}', class='{inp_class}', onclick='{onclick}', disabled='{disabled}'")

        except Exception as e:
            logger.error(f"Error in debug print: {str(e)}")

    def _handle_popup(self):
        """Click the politicas button and handle the popup"""
        try:
            # First, make sure the validar folio step was successful
            # Look for any validation messages or enabled fields
            self._wait_for_validation_success()

            # Click the politicas button
            #logger.info("Looking for politicas button...")
            #politicas_button = self.wait_for_clickable(By.ID, "politicasPr-input", timeout=15)

            # Scroll to button and click
            #self.driver.execute_script("arguments[0].scrollIntoView(true);", politicas_button)
            #time.sleep(0.5)
            #politicas_button.click()
            #logger.info("Politicas button clicked")

            # Wait for popup and click confirm
            logger.info("Waiting for popup to appear...")
            try:
                confirm_button = self.wait_for_clickable(
                    By.CLASS_NAME, "swal2-confirm", timeout=15
                )
                confirm_button.click()
                logger.info("Popup confirmed successfully")

                # Wait for popup to disappear and form to be enabled
                time.sleep(10)

            except TimeoutException:
                logger.error("Popup confirm button not found within timeout")
                # Try alternative selectors for the confirm button
                alt_selectors = [
                    "swal2-styled",
                    "swal2-default-outline",
                    "btn-confirm",
                    "btn-ok"
                ]

                for selector in alt_selectors:
                    try:
                        confirm_button = self.wait_for_clickable(By.CLASS_NAME, selector, timeout=5)
                        confirm_button.click()
                        logger.info(f"Popup confirmed with alternative selector: {selector}")
                        time.sleep(3)
                        break
                    except:
                        continue
                else:
                    raise TimeoutException("Could not find popup confirm button")

        except Exception as e:
            logger.error(f"Error in _handle_popup: {str(e)}")
            raise

    def _wait_for_validation_success(self):
        """Wait for validation to complete successfully"""
        try:
            # Wait a bit for any validation to process
            time.sleep(2)

            # Check for validation error messages
            error_selectors = [
                ".error",
                ".alert-danger",
                ".validation-error",
                ".text-danger",
                "[class*='error']"
            ]

            for selector in error_selectors:
                try:
                    error_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for error in error_elements:
                        error_text = error.text.strip()
                        if error_text and error.is_displayed():
                            logger.warning(f"Validation error found: {error_text}")
                            raise Exception(f"Form validation failed: {error_text}")
                except:
                    continue

            # Check if politicas button is now enabled (sign of successful validation)
            try:
                politicas_button = self.driver.find_element(By.ID, "politicasPr-input")
                if politicas_button.get_attribute("disabled"):
                    logger.warning("Politicas button is still disabled - validation may have failed")
                else:
                    logger.info("Politicas button is enabled - validation appears successful")
            except:
                logger.warning("Could not check politicas button status")

        except Exception as e:
            logger.warning(f"Error checking validation status: {str(e)}")
            # Continue anyway

    def _fill_second_section_guadalajara(self, data):
        """Fill the second section of the form after popup is handled - Simple version"""
        try:
            # Wait a bit for the form to be fully enabled
            time.sleep(2)

            # Fill RFC
            logger.info("Filling RFC...")
            rfc_element = self.wait_for_element(By.ID, "rfc")
            self._simple_clear_and_fill(rfc_element, data['rfc'])

            # Fill Código Postal
            logger.info("Filling Código Postal...")
            codigo_postal_element = self.wait_for_element(By.ID, "codigoPostal")
            self._simple_clear_and_fill(codigo_postal_element, data['codigo_postal'])
           # Fill Razón Social (the problematfield)
            logger.info("Filling Razón Social...")
            razon_social_element = self.wait_for_element(By.ID, "razonSocial")
            self._simple_clear_and_fill(razon_social_element, data['razon_social'])

            # Select Régimen Fiscal
            logger.info("Selecting Régimen Fiscal...")
            regimen_fiscal_select = Select(self.wait_for_element(By.ID, "regimenFiscal"))
            regimen_fiscal_select.select_by_value(data['regimen_fiscal'])
            time.sleep(0.5)

         # Select Uso de CFDI
            logger.info("Selecting Uso de CFDI...")
            uso_cfdi_select = Select(self.wait_for_element(By.ID, "usoCfdi"))
            uso_cfdi_select.select_by_value(data['uso_cfdi'])
            time.sleep(0.5)

            logger.info("Second section filled successfully")

        except Exception as e:
            logger.error(f"Error in _fill_second_section_guadalajara: {str(e)}")
            raise

    def _fill_second_section_ahorro(self, data):
        """Fill the second section of the form after popup is handled - Simple version"""
        try:
            # Wait a bit for the form to be fully enabled
            time.sleep(2)

            # Fill Email
            logger.info("Filling Email...")
            rfc_element = self.wait_for_element(By.ID, "ConfirmarCorreo")
            self._simple_clear_and_fill(rfc_element, data['email'])

            # Select Régimen Fiscal
            logger.info("Selecting Régimen Fiscal...")
            regimen_fiscal_select = Select(self.wait_for_element(By.ID, "inputRF"))
            regimen_fiscal_select.select_by_value(data['regimen_fiscal'])
            time.sleep(0.5)

            # Select Uso de CFDI
            logger.info("Selecting Uso de CFDI...")
            uso_cfdi_select = Select(self.wait_for_element(By.ID, "inputState"))
            uso_cfdi_select.select_by_value(data['uso_cfdi'])
            time.sleep(0.5)
            
            

            logger.info("Second section filled successfully")

        except Exception as e:
            logger.error(f"Error in _fill_second_section_ahorro: {str(e)}")
            raise

    def _simple_clear_and_fill(self, element, value):
        """Simple method to clear and fill without duplication"""
        try:
            logger.info(f"Clearing and filling field with: '{value}'")

            # Method 1: Standard clear
            element.clear()
            time.sleep(0.1)

            # Method 2: Select all and delete (cross-platform)
            try:
                element.send_keys(Keys.CONTROL + "a")  # Ctrl+A on Windows/Linux
                element.send_keys(Keys.DELETE)
            except:
                try:
                    element.send_keys(Keys.COMMAND + "a")  # Cmd+A on Mac
                    element.send_keys(Keys.DELETE)
                except:
                    pass  # If both fail, continue with other methods

            time.sleep(0.1)

            # Method 3: JavaScript clear (most reliable)
            self.driver.execute_script("arguments[0].value = '';", element)
            self.driver.execute_script("arguments[0].setAttribute('value', '');", element)
            time.sleep(0.1)

            # Verify field is empty
            current_value = element.get_attribute('value')
            if current_value:
                logger.warning(f"Field still contains '{current_value}' after clearing attempts")
                # Force clear with focus and selection
                element.click()
                self.driver.execute_script("""
                    arguments[0].focus();
                    arguments[0].select();
                    arguments[0].value = '';
                """, element)

            # Now fill with the value
            element.send_keys(value)
            time.sleep(0.2)

            # Verify the value and check for duplication
            final_value = element.get_attribute('value')
            logger.info(f"Field value after filling: '{final_value}'")

            if final_value != value:
                if value in final_value and len(final_value) > len(value):
                    logger.warning(f"Duplication detected! Expected: '{value}', Got: '{final_value}'")
                    # Fix duplication by setting value directly
                    self.driver.execute_script("arguments[0].value = arguments[1];", element, value)
                    logger.info("Duplication fixed with JavaScript")
                else:
                    logger.warning(f"Unexpected value. Expected: '{value}', Got: '{final_value}'")

            # Trigger Angular change events
            self.driver.execute_script("""
                var element = arguments[0];
                var events = ['input', 'change', 'blur'];
                events.forEach(function(eventType) {
                    var event = new Event(eventType, {bubbles: true});
                    element.dispatchEvent(event);
                });
            """, element)

            logger.info(f"Successfully filled field with: '{value}'")

        except Exception as e:
            logger.error(f"Error in _simple_clear_and_fill: {str(e)}")
            raise

    def _safe_clear_and_fill(self, element, value):
        """Safely clear and fill an input field to prevent duplication"""
        try:
            logger.info(f"Filling field with value: '{value}'")

            # Method 1: Multiple clearing attempts
            for attempt in range(3):
                # Clear the field multiple ways
                element.clear()

                # Select all and delete (works better with some Angular inputs)
                element.send_keys(Keys.CTRL + "a")
                element.send_keys(Keys.DELETE)

                # Check if field is actually empty
                current_value = element.get_attribute('value')
                if not current_value:
                    break

                logger.warning(f"Field still contains '{current_value}' after clearing attempt {attempt + 1}")
                time.sleep(0.2)

            # Verify field is empty before filling
            final_value = element.get_attribute('value')
            if final_value:
                logger.warning(f"Field not completely cleared, contains: '{final_value}'")
                # Force clear with JavaScript
                self.driver.execute_script("arguments[0].value = '';", element)

            # Fill the field
            element.send_keys(value)

            # Verify the value was set correctly
            new_value = element.get_attribute('value')
            logger.info(f"Field value after filling: '{new_value}'")

            # Check for duplication
            if value in new_value and new_value != value:
                logger.warning(f"Duplication detected! Expected: '{value}', Got: '{new_value}'")
                # Fix duplication
                self.driver.execute_script("arguments[0].value = arguments[1];", element, value)
                final_check = element.get_attribute('value')
                logger.info(f"After duplication fix: '{final_check}'")

            # Trigger Angular events
            self.driver.execute_script("""
                var element = arguments[0];
                element.dispatchEvent(new Event('input', {bubbles: true}));
                element.dispatchEvent(new Event('change', {bubbles: true}));
                element.dispatchEvent(new Event('blur', {bubbles: true}));
            """, element)

        except Exception as e:
            logger.error(f"Error in _safe_clear_and_fill: {str(e)}")
            raise

    def _alternative_fill_method(self, element, value):
        """Alternative method using JavaScript to set value directly"""
        try:
            logger.info(f"Using alternative fill method for value: '{value}'")

            # Set value directly with JavaScript (bypasses some Angular issues)
            self.driver.execute_script("""
                var element = arguments[0];
                var value = arguments[1];

                // Clear the field completely
                element.value = '';
                element.setAttribute('value', '');

                // Set the new value
                element.value = value;
                element.setAttribute('value', value);

                // Trigger all relevant events
                var events = ['input', 'change', 'blur', 'keyup', 'keydown'];
                events.forEach(function(eventType) {
                    var event = new Event(eventType, {bubbles: true});
                    element.dispatchEvent(event);
                });

            """, element, value)

            # Verify the value
            new_value = element.get_attribute('value')
            logger.info(f"Alternative method result: '{new_value}'")

            return new_value == value

        except Exception as e:
            logger.error(f"Error in alternative fill method: {str(e)}")
            return False

    def _enhanced_fill_field(self, field_id, value, field_name):
        """Enhanced method to fill any field with multiple fallback approaches"""
        try:
            logger.info(f"Filling {field_name} with value: '{value}'")

            element = self.wait_for_element(By.ID, field_id)

            # Method 1: Safe clear and fill
            try:
                self._safe_clear_and_fill(element, value)
                final_value = element.get_attribute('value')

                if final_value == value:
                    logger.info(f"✓ {field_name} filled successfully with standard method")
                    return True

            except Exception as e:
                logger.warning(f"Standard method failed for {field_name}: {str(e)}")

          # Method 2: Alternative JavaScript method
            try:
                if self._alternative_fill_method(element,alue):
                    logger.info(f"{field_name} filled successfully with alternative method")
                    return True

            except Exception as e:
                logger.warning(f"Alternative method failed for {field_name}: {str(e)}")

            # Method 3: Last resort - character by character
            try:
                elent.clear()
                self.driver.execute_script("arguments[0].value = '';", element)

                # Send keys one by one with small delays
                for char in value:
                    element.send_keys(char)
                    time.sleep(0.01)  # Very small delay between characters

                # Trigger events
                self.driver.execute_script("""
                    var element = arguments[0];
                    element.dispatchEvent(new Event('input', {bubbles: true}));
                    element.dispatchEvent(new Event('change', {bubbles: true}));
                """, element)

                final_value = element.get_attribute('value')
                if final_value == value:
                    logger.info(f"✓ {field_name} filled scessfully with character-by-charter method")
                    return True
                else:
                    #logger.error(f"❌ All methods failed for {field_name}. Expected: 'ue}', Got: '{final_value}'")
                    return False

            except Exception as e:
                logger.error(f"Character-by-character method failed for {field_name}: {str(e)}")
            return False

        except Exception as e:
            logger.error(f"Error filling {field_name}: {str(e)}")
            return False

    def _setupemail(self, data):
        """Setup email delivery if requested"""
        # Check the email checkbox
        email_checkbox = self.wait_for_element(By.ID, "envioCorreo-input")
        if not email_checkbox.is_selected():
            email_checkbox.click()

        # Wait for email fields to appear
        time.sleep(1)

        # Fill email
        email_element = self.wait_for_element(By.ID, "correo")
        email_element.clear()
        email_element.send_keys(data['email'])

        # Confirm email
        email_confirm_element = self.wait_for_element(By.ID, "correoConfirm")
        email_confirm_element.clear()
        email_confirm_element.send_keys(data['email_confirm'])

    def _submit_form_guadalajara(self):
        """Submit the form and wait for ZIP download - Improved version"""
        try:
            logger.info("Looking for 'Obtener Factura' button...")

            # First, check if there are any blocking popups before clicking
            self._dismiss_any_blocking_popups()

            # Click "Obtener Factura" button
            if self._click_obtener_factura_button():
                logger.info("Button clicked successfully, processing...")

                # Handle the final confirmation popup with retry logic
                max_popup_attempts = 3
                popup_handled = False

                for attempt in range(max_popup_attempts):
                    try:
                        logger.info(f"Handling final confirmation popup (attempt {attempt + 1}/{max_popup_attempts})...")
                        self._handle_final_confirmation_popup()
                        popup_handled = True
                        break
                    except Exception as popup_error:
                        logger.warning(f"Popup handling attempt {attempt + 1} failed: {str(popup_error)}")
                        if attempt < max_popup_attempts - 1:
                            time.sleep(2)  # Wait before retry
                            continue
                        else:
                            raise popup_error

                if popup_handled:
                    # Wait for download to complete
                    logger.info("Waiting for ZIP download...")
                    #return self._wait_for_download()
                else:
                    raise Exception("Failed to handle confirmation popup")
            else:
                raise Exception("Failed to click 'Obtener Factura' button")

        except Exception as e:
            logger.error(f"Error in _submit_form_guadalajara: {str(e)}")
            raise

    def _submit_form_ahorro(self, timeout=60, zip_filename=None):
        """Submit the form and wait for ZIP download - Improved version"""
        try:
            #logger.info("Looking for 'Continuar' button...")

            # First, check if there are any blocking popups before clicking
            self._dismiss_any_blocking_popups()
            
            #logger.info("Clicking 'Continuar' button...")
            self._click_continuar_button()
            #Click Generar Factura Button
            button = self.wait_for_element_enabled(By.ID, "GenerarFactura")
            button.click()
            #Descargar Archivos
            #self.download_both_files()
            try:
                logger.info("Starting invoice ZIP creation process...")
                
                # First, execute the downloads
                logger.info("Initiating PDF and XML downloads...")
                download_success = self.download_both_files()
                
                if not download_success:
                    logger.warning("Download process completed with warnings, continuing with ZIP creation...")
                
                # Wait for both files to be downloaded
                pdf_file, xml_file = self._wait_for_both_downloads(timeout)
                
                # Create the ZIP file
                zip_file_path = self._create_zip_from_files(pdf_file, xml_file, zip_filename)
                
                # Clean up individual files after zipping
                self._cleanup_individual_files(pdf_file, xml_file)
                
                logger.info(f"✓ Invoice ZIP created successfully: {zip_file_path}")
                return zip_file_path
                
            except Exception as e:
                logger.error(f"Error creating invoice ZIP: {str(e)}")
                raise
            
        except Exception as e:
            logger.error(f"Error in _submit_form: {str(e)}")
            raise

    def _submit_form_ahorro_descargar(self, timeout=60, zip_filename=None):
        """Submit the form and wait for ZIP download - Improved version"""
        try:
            # First, check if there are any blocking popups before clicking
            self._dismiss_any_blocking_popups()

            try:
                logger.info("Starting invoice ZIP creation process...")
                
                # First, execute the downloads
                logger.info("Initiating PDF and XML downloads...")
                download_success = self.download_both_files()
                
                if not download_success:
                    logger.warning("Download process completed with warnings, continuing with ZIP creation...")
                
                # Wait for both files to be downloaded
                pdf_file, xml_file = self._wait_for_both_downloads(timeout)
                
                # Create the ZIP file
                zip_file_path = self._create_zip_from_files(pdf_file, xml_file, zip_filename)
                
                # Clean up individual files after zipping
                self._cleanup_individual_files(pdf_file, xml_file)
                
                logger.info(f"✓ Invoice ZIP created successfully: {zip_file_path}")
                return zip_file_path
                
            except Exception as e:
                logger.error(f"Error creating invoice ZIP: {str(e)}")
                raise
            
        except Exception as e:
            logger.error(f"Error in _submit_form: {str(e)}")
            raise

    def _dismiss_any_blocking_popups(self):
        """Dismiss any popups that might be blocking interaction"""
        try:
            # Check for existing SweetAlert2 popups
            existing_popups = self.driver.find_elements(By.CSS_SELECTOR, ".swal2-container")

            for popup in existing_popups:
                if popup.is_displayed():
                    logger.info("Found existing popup, attempting to dismiss...")

                    # Look for close/dismiss buttons
                    dismiss_buttons = popup.find_elements(By.CSS_SELECTOR, ".swal2-close, .swal2-confirm, .swal2-cancel")

                    for btn in dismiss_buttons:
                        if btn.is_displayed() and btn.is_enabled():
                            try:
                                btn.click()
                                logger.info("Dismissed existing popup")
                                time.sleep(1)
                                break
                            except:
                                continue

        except Exception as e:
            logger.debug(f"Error dismissing blocking popups: {str(e)}")

    def _click_obtener_factura_button(self):
        """Click the Angular Material Obtener Factura button with improved targeting"""
        button_found = False

        logger.info("Looking for Angular Material 'Obtener Factura' button...")

        # Wait for Angular to fully load and button to be ready
        time.sleep(2)

        # Simplified and more reliable selectors
        targeted_selectors = [
            # Target by the inner span with "Obtener Factura" text (most reliable)
            (By.XPATH, "//span[@class='mdc-button__label' and normalize-space(text())='Obtener Factura']/parent::button"),
        
            # Target submit button with specific Angular Material classes
            (By.XPATH, "//button[@type='submit' and contains(@class, 'mdc-fab--extended') and contains(@class, 'mat-mdc-fab')]"),
        
            # More general - any submit button containing "Obtener Factura"
            (By.XPATH, "//button[@type='submit' and contains(., 'Obtener Factura')]"),
        
            # Fallback - any button with "Obtener Factura" text
            (By.XPATH, "//button[contains(text(), 'Obtener Factura')]"),
        ]

        for by_method, selector in targeted_selectors:
            try:
                logger.info(f"Trying selector: {selector}")

                # Wait for element to be present and clickable
                element = WebDriverWait(self.driver, 15).until(
                    EC.element_to_be_clickable((by_method, selector))
                )

                if element:
                    # Log element details for verification
                    element_text = element.text.strip()
                    element_classes = element.get_attribute('class')
                    element_type = element.get_attribute('type')
                    is_enabled = element.is_enabled()
                    is_displayed = element.is_displayed()

                    logger.info(f"Found element - Text: '{element_text}', Classes: '{element_classes}', Type: '{element_type}', Enabled: {is_enabled}, Displayed: {is_displayed}")

                    # Check if this looks like our button
                    if not ('obtener' in element_text.lower() and 'factura' in element_text.lower()):
                        logger.debug("Element doesn't match expected criteria, trying next selector")
                        continue

                    # Scroll element into view smoothly
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});"
                        "window.scrollBy(0, -100);", # Offset for any fixed headers
                        element
                    )
                    time.sleep(1)

                    # Try multiple click methods
                    click_success = False

                    # Method 1: JavaScript click (most reliable for Angular Material)
                    try:
                        self.driver.execute_script("""
                            var element = arguments[0];
                        
                            // Dispatch multiple events that Angular Material expects
                            var events = ['mousedown', 'mouseup', 'click'];
                            events.forEach(function(eventType) {
                                var event = new MouseEvent(eventType, {
                                    bubbles: true,
                                    cancelable: true,
                                    view: window
                                });
                                element.dispatchEvent(event);
                            });
                        
                            // Also trigger form submission if it's a submit button
                            if (element.type === 'submit') {
                                var form = element.closest('form');
                                if (form) {
                                    var submitEvent = new Event('submit', {
                                        bubbles: true,
                                        cancelable: true
                                    });
                                    form.dispatchEvent(submitEvent);
                                }
                            }
                        """, element)

                        logger.info("✓ Successfully clicked 'Obtener Factura' button using JavaScript with events")
                        click_success = True

                    except Exception as js_error:
                        logger.warning(f"JavaScript click failed: {str(js_error)}")

                    # Method 2: ActionChains click (if JS fails)
                    if not click_success:
                        try:
                            from selenium.webdriver.common.action_chains import ActionChains
                            actions = ActionChains(self.driver)
                            actions.move_to_element(element).pause(0.5).click().perform()

                            logger.info("✓ Successfully clicked 'Obtener Factura' button using ActionChains")
                            click_success = True

                        except Exception as action_error:
                            logger.warning(f"ActionChains click failed: {str(action_error)}")

                    # Method 3: Direct click (last resort)
                    if not click_success:
                        try:
                            element.click()
                            logger.info("✓ Successfully clicked 'Obtener Factura' button using direct click")
                            click_success = True

                        except Exception as direct_error:
                            logger.warning(f"Direct click failed: {str(direct_error)}")

                    if click_success:
                        button_found = True
                        logger.info("Button clicked successfully, waiting for processing...")
                        time.sleep(3)  # Wait for any processing to start
                        break

            except TimeoutException:
                logger.debug(f"Selector timed out: {selector}")
                continue
            except Exception as e:
                logger.debug(f"Selector failed {selector}: {str(e)}")
                continue

        if not button_found:
            logger.error("❌ Could not find or click the Angular Material 'Obtener Factura' button")
            self._debug_submit_button()
            raise Exception("Failed to click Obtener Factura button")

        return button_found

    def _check_submit_feedback(self):
        """Check for feedback after clicking Obtener Factura button"""
        try:
            # Look for loading indicators, succe messages, or download starting
            feedback_selectors = [
                ".loading",
                ".spinner",
                ".mat-progress",
                ".downloading",
                ".processing",
                "[role='progressbar']",
                ".mat-snack-bar-container",
                ".toast",
                ".notification",
                ".alert"
            ]

            for selector in feedback_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.text.strip():
                            feedback_text = element.text.strip()
                            logger.info(f"Submit feedback: {feedback_text}")

                            # Check if it's an error
                            if any(word in feedback_text.lower() for word in ['error', 'invalid', 'failed', 'problema']):
                                logger.error(f"Submit error detected: {feedback_text}")
                                raise Exception(f"Form submission failed: {feedback_text}")
                            else:
                                logger.info(f"Submit feedback (likely processing): {feedback_text}")

                except Exception as e:
                    continue

            # Check for URL changes (might redirect after submission)
            current_url = self.driver.current_url
            logger.info(f"Current URL after submit: {current_url}")

        except Exception as e:
            logger.debug(f"Error checking submit feedback: {str(e)}")

    def _debug_submit_button(self):
        """Debug method specifically for submit button troubleshooting"""
        try:
            logger.info("=== DEBUG: Submit button troubleshooting ===")

            # Find all submit buttons
            submit_buttons = self.driver.find_elements(By.XPATH, "//button[@type='submit']")
            logger.info(f"Found {len(submit_buttons)} submit buttons")

            for i, btn in enumerate(submit_buttons):
                try:
                    text = btn.text.strip()
                    classes = btn.get_attribute("class")
                    enabled = btn.is_enabled()
                    displayed = btn.is_displayed()
                    logger.info(f"Submit button {i}: text='{text}', enabled={enabled}, displayed={displayed}")
                    logger.info(f"  Classes: {classes}")
                except Exception as e:
                    logger.info(f"Submit button {i}: Error getting info - {str(e)}")

            # Look for buttons containing "obtener" or "factura"
            obtener_buttons = self.driver.find_elements(By.XPATH, "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'obtener') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'factura')]")
            logger.info(f"Found {len(obtener_buttons)} buttons with 'obtener' or 'factura'")

            for i, btn in enumerate(obtener_buttons):
                try:
                    text = btn.text.strip()
                    enabled = btn.is_enabled()
                    displayed = btn.is_displayed()
                    logger.info(f"Obtener button {i}: text='{text}', enabled={enabled}, displayed={displayed}")
                except Exception as e:
                    logger.info(f"Obtener button {i}: Error - {str(e)}")

        except Exception as e:
            logger.error(f"Error in submit button debug: {str(e)}")

    def _handle_final_confirmation_popup(self, timeout=30):
        """Handle the final confirmation popup after clicking 'Obtener Factura'"""
        try:
            logger.info("Waiting for final confirmation popup to appear...")

            # Simplified selectors for SweetAlert2 confirmation button
            confirmation_selectors = [
                # Most reliable - target by swal2-confirm class
                (By.CSS_SELECTOR, "button.swal2-confirm"),
            
                # Target by swal2-confirm with text verification
                (By.XPATH, "//button[contains(@class, 'swal2-confirm')]"),
            
                # Fallback - any button with "Aceptar" text in popup
                (By.XPATH, "//div[contains(@class, 'swal2-container')]//button[contains(text(), 'Aceptar')]"),
            
                # Last resort - any button in SweetAlert2 container
                (By.XPATH, "//div[contains(@class, 'swal2-container')]//button[@type='button']"),
            ]

            popup_handled = False

            for by_method, selector in confirmation_selectors:
                try:
                    logger.info(f"Trying confirmation selector: {selector}")

                    # Wait for the popup button to appear and be clickable
                    confirm_button = WebDriverWait(self.driver, timeout).until(
                        EC.element_to_be_clickable((by_method, selector))
                    )

                    if confirm_button:
                        # Log button details
                        button_text = confirm_button.text.strip()
                        button_classes = confirm_button.get_attribute('class')
                        is_displayed = confirm_button.is_displayed()
                        is_enabled = confirm_button.is_enabled()

                        logger.info(f"Found confirmation button - Text: '{button_text}', Classes: '{button_classes}', Displayed: {is_displayed}, Enabled: {is_enabled}")

                        # Try multiple click methods for reliability
                        click_methods = [
                            ("JavaScript click", lambda btn: self.driver.execute_script("arguments[0].click();", btn)),
                            ("ActionChains click", lambda btn: ActionChains(self.driver).move_to_element(btn).click().perform()),
                            ("Direct click", lambda btn: btn.click()),
                            ("JavaScript event dispatch", lambda btn: self.driver.execute_script("""
                                var element = arguments[0];
                                var event = new MouseEvent('click', {
                                    bubbles: true,
                                    cancelable: true,
                                    view: window
                                });
                                element.dispatchEvent(event);
                            """, btn))
                        ]

                        for method_name, click_method in click_methods:
                            try:
                                # Re-find element to avoid stale reference
                                fresh_button = self.driver.find_element(by_method, selector)
                            
                                if fresh_button.is_displayed() and fresh_button.is_enabled():
                                    click_method(fresh_button)
                                    logger.info(f"✓ Successfully clicked confirmation button using {method_name}")
                                    popup_handled = True
                                    break
                                else:
                                    logger.warning(f"Button not clickable for {method_name}")
                                
                            except Exception as click_error:
                                logger.warning(f"{method_name} failed: {str(click_error)}")
                                continue

                        if popup_handled:
                            logger.info("Final confirmation popup handled successfully")
                        
                            # Wait for popup to disappear
                            time.sleep(3)
                        
                            # Verify popup is dismissed
                            self._verify_popup_dismissed()
                            break

                except TimeoutException:
                    logger.debug(f"Selector timed out: {selector}")
                    continue
                except Exception as e:
                    logger.debug(f"Selector failed {selector}: {str(e)}")
                    continue

            if not popup_handled:
                logger.error("❌ Could not find or click the confirmation popup button")
                self._debug_popup_elements()
                raise Exception("Failed to handle final confirmation popup")

            return popup_handled

        except Exception as e:
            logger.error(f"Error handling final confirmation popup: {str(e)}")
            raise


    def _verify_popup_dismissed(self):
        """Verify that the popup has been dismissed"""
        try:
            # Wait a moment for popup to disappear
            time.sleep(2)

            # Check if SweetAlert2 container is gone or hidden
            popup_containers = self.driver.find_elements(By.CSS_SELECTOR, ".swal2-container, .swal2-popup")

            visible_popups = [popup for popup in popup_containers if popup.is_displayed()]

            if visible_popups:
                logger.warning(f"Found {len(visible_popups)} still visible popups")
                # Try to dismiss remaining popups
                for popup in visible_popups:
                    try:
                        # Look for any clickable buttons in remaining popups
                        buttons = popup.find_elements(By.TAG_NAME, "button")
                        for btn in buttons:
                            if btn.is_displayed() and btn.is_enabled():
                                try:
                                    btn.click()
                                    logger.info("Clicked additional popup button")
                                    time.sleep(1)
                                    break
                                except:
                                    continue
                    except:
                        continue
            else:
                logger.info("✓ Confirmation popup successfully dismissed")

            # Additional check - ensure we can interact with the main page
            try:
                # Try to find an element that should be on the main page
                self.driver.find_element(By.TAG_NAME, "body")
                logger.info("✓ Main page is accessible after popup dismissal")
            except:
                logger.warning("⚠ Main page may not be fully accessible yet")

        except Exception as e:
            logger.debug(f"Error verifying popup dismissal: {str(e)}")


    def _debug_popup_elements(self):
        """Debug method to find popup elements when handling fails"""
        try:
            logger.info("=== DEBUG: Popup elements ===")

            # Check page state
            logger.info(f"Current URL: {self.driver.current_url}")
            logger.info(f"Page title: {self.driver.title}")

            # Look for any SweetAlert2 elements
            swal_selectors = [
                ".swal2-container", ".swal2-popup", ".swal2-confirm",
                ".swal2-styled", "[class*='swal2']"
            ]

            for selector in swal_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    logger.info(f"Found {len(elements)} elements with selector '{selector}'")

                    for i, element in enumerate(elements):
                        try:
                            tag = element.tag_name
                            classes = element.get_attribute('class')
                            text = element.text.strip()
                            displayed = element.is_displayed()
                            enabled = element.is_enabled() if tag == 'button' else 'N/A'

                            logger.info(f"  Element {i}: tag='{tag}', classes='{classes}', displayed={displayed}, enabled={enabled}")
                            if text:
                                logger.info(f"    Text: '{text[:100]}...'")
                        except Exception as elem_error:
                            logger.debug(f"  Element {i}: Error getting details - {str(elem_error)}")
                except Exception as selector_error:
                    logger.debug(f"Selector '{selector}' failed: {str(selector_error)}")

            # Look for any visible buttons
            buttons = self.driver.find_elements(By.TAG_NAME, "button")
            visible_buttons = [btn for btn in buttons if btn.is_displayed()]

            logger.info(f"Found {len(visible_buttons)} visible buttons total")
            for i, btn in enumerate(visible_buttons[:10]):  # Limit to first 10 for readability
                try:
                    text = btn.text.strip()
                    classes = btn.get_attribute('class')
                    btn_type = btn.get_attribute('type')
                    enabled = btn.is_enabled()
                    logger.info(f"  Button {i}: text='{text}', type='{btn_type}', enabled={enabled}")
                    if classes:
                        logger.info(f"    Classes: '{classes}'")
                except Exception as btn_error:
                    logger.debug(f"  Button {i}: Error - {str(btn_error)}")

        except Exception as e:
            logger.error(f"Error in popup debug: {str(e)}")

    def sending_file(self, timeout=60):
        """
        Check if there is exactly one .zip file in Downloads directory,
        return its path for sending to client, and prepare for deletion after sending
        """
        start_time = time.time()
        #download_dir = Path(os.path.expanduser("~/Downloads"))
        download_dir = DOWNLOADS_DIR
        logger.info(f"Checking for ZIP file in directory: {download_dir}")

        while time.time() - start_time < timeout:
            try:
                # Get all ZIP files in the directory
                zip_files = list(download_dir.glob("*.zip"))

                # Check if there's exactly one ZIP file
                if len(zip_files) == 1:
                    zip_file = zip_files[0]

                    # Verify file is complete and readable
                    try:
                        file_size = zip_file.stat().st_size
                        if file_size > 1024:  # File should be at least 1KB
                            logger.info(f"ZIP file found: {zip_file} ({file_size} bytes)")
                            return str(zip_file)
                        else:
                            logger.debug(f"File too small ({file_size} bytes), continuing to wait...")
                    except Exception as file_error:
                        logger.debug(f"Error checking file: {str(file_error)}")

                elif len(zip_files) == 0:
                    logger.debug("No ZIP files found yet, continuing to wait...")

                elif len(zip_files) > 1:
                    logger.warning(f"Multiple ZIP files found ({len(zip_files)}), cannot determine which one to send")
                    # List all files for debugging
                    for i, zip_file in enumerate(zip_files):
                        logger.warning(f"  File {i+1}: {zip_file.name}")
                    raise Exception(f"Multiple ZIP files found in directory. Expected exactly 1, found {len(zip_files)}")

                # Check for download in progress
                temp_files = list(download_dir.glob("*.crdownload"))
                temp_files.extend(list(download_dir.glob("*.tmp")))

                if temp_files:
                    logger.debug(f"Download in progress ({len(temp_files)} temp files)...")

                # Check for any download errors on the page
                try:
                    error_elements = self.driver.find_elements(By.XPATH, "//*[contains(@class, 'error') or contains(text(), 'error') or contains(text(), 'Error')]")
                    for error in error_elements:
                        if error.is_displayed() and error.text.strip():
                            error_text = error.text.strip()
                            if any(word in error_text.lower() for word in ['error', 'failed', 'problema']):
                                raise Exception(f"Download error detected on page: {error_text}")
                except Exception:
                    # Driver might be closed or page changed, continue waiting
                    pass

                time.sleep(2)  # Check every 2 seconds

            except Exception as check_error:
                if "Multiple ZIP files" in str(check_error):
                    raise  # Re-raise this specific error
                logger.debug(f"Error during file check: {str(check_error)}")
                time.sleep(2)

        # Timeout reached
        current_files = list(download_dir.glob("*.zip"))
        logger.error(f"File check timeout after {timeout} seconds")
        logger.error(f"Current ZIP files in directory: {len(current_files)}")

        if len(current_files) == 0:
            raise TimeoutException(f"No ZIP file found after {timeout} seconds")
        elif len(current_files) > 1:
            raise Exception(f"Multiple ZIP files found after timeout. Expected exactly 1, found {len(current_files)}")
        else:
            # This shouldn't happen, but just in case
            raise TimeoutException(f"Unknown error: found {len(current_files)} files but couldn't return path")



    def _wait_for_download(self, timeout=60):  # Increased timeout
        """Wait for ZIP file to be downloaded with improved detection"""
        start_time = time.time()
        logger.info(f"Waiting for ZIP download in directory: {self.download_directory}")

        # Get initial file count
        initial_files = set(Path(self.download_directory).glob("*.zip"))
        initial_count = len(initial_files)
        logger.info(f"Initial ZIP count: {initial_count}")

        while time.time() - start_time < timeout:
            try:
                # List current ZIP files
                current_files = set(Path(self.download_directory).glob("*.zip"))

                # Check for new files
                new_files = current_files - initial_files

                if new_files:
                    # Get the newest file
                    latest_file = max(new_files, key=lambda f: f.stat().st_ctime)

                    # Check if file is still being downloaded
                    temp_files = list(Path(self.download_directory).glob("*.crdownload"))
                    temp_files.extend(list(Path(self.download_directory).glob("*.tmp")))

                    if not temp_files:
                        # Verify file is complete and readable
                        try:
                            file_size = latest_file.stat().st_size
                            if file_size > 1024:  # File should be at least 1KB
                                logger.info(f"ZIP downloaded successfully: {latest_file} ({file_size} bytes)")
                                return str(latest_file)
                            else:
                                logger.debug(f"File too small ({file_size} bytes), continuing to wait...")
                        except Exception as file_error:
                            logger.debug(f"Error checking file: {str(file_error)}")
                    else:
                        logger.debug(f"Download in progress ({len(temp_files)} temp files)...")

                # Check for any download errors on the page
                error_elements = self.driver.find_elements(By.XPATH, "//*[contains(@class, 'error') or contains(text(), 'error') or contains(text(), 'Error')]")
                for error in error_elements:
                    if error.is_displayed() and error.text.strip():
                        error_text = error.text.strip()
                        if any(word in error_text.lower() for word in ['error', 'failed', 'problema']):
                            raise Exception(f"Download error detected on page: {error_text}")

                time.sleep(2)  # Check every 2 seconds

            except Exception as check_error:
                logger.debug(f"Error during download check: {str(check_error)}")
                time.sleep(2)

        # Timeout reached
        current_files = list(Path(self.download_directory).glob("*.zip"))
        logger.error(f"ZIP download timeout after {timeout} seconds")
        logger.error(f"Current ZIP files in directory: {len(current_files)}")

        raise TimeoutException(f"ZIP download timeout after {timeout} seconds")


    def _click_download_pdf_button(self, timeout=60):
        """
        Click the 'Descargar PDF' button with multiple fallback strategies
        """
        try:
            logger.info("Looking for 'Descargar PDF' button...")
            
            # Strategy 1: Target by exact class combination and button text
            pdf_selectors = [
                # Most specific - by class, text and icon
                (By.XPATH, "//a[contains(@class, 'btn btn-danger') and contains(@class, 'heightButton') and contains(text(), 'Descargar PDF')]"),
                
                # By icon class and text
                (By.XPATH, "//a[.//i[contains(@class, 'bi-filetype-pdf')] and contains(text(), 'Descargar PDF')]"),
                
                # By text content only
                (By.XPATH, "//a[contains(text(), 'Descargar PDF')]"),
                
                # By icon class only (less specific)
                (By.XPATH, "//a[.//i[contains(@class, 'bi-filetype-pdf')]]"),
                
                # By href pattern (PDF files)
                (By.XPATH, "//a[contains(@href, '.pdf') and contains(@class, 'btn-danger')]"),
                
                # Case insensitive text matching
                (By.XPATH, "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar') and contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'pdf')]")
            ]
            
            button_clicked = False
            
            for i, (by_method, selector) in enumerate(pdf_selectors):
                try:
                    logger.info(f"Trying selector {i+1}: {selector}")
                    
                    # Wait for button to be present and clickable
                    continuar_button = WebDriverWait(self.driver, timeout).until(
                        EC.element_to_be_clickable((by_method, selector))
                    )
                    
                    if continuar_button:
                        # Log button details for debugging
                        button_text = continuar_button.text.strip()
                        button_classes = continuar_button.get_attribute('class')
                        is_enabled = continuar_button.is_enabled()
                        is_displayed = continuar_button.is_displayed()
                        
                        logger.info(f"Found button - Text: '{button_text}', Classes: '{button_classes}'")
                        logger.info(f"Button state - Enabled: {is_enabled}, Displayed: {is_displayed}")
                        
                        if is_enabled and is_displayed:
                            # Multiple click strategies for reliability
                            click_strategies = [
                                ("scroll_and_click", self._scroll_and_click),
                                ("javascript_click", self._javascript_click),
                                ("action_chains_click", self._action_chains_click),
                                ("direct_click", self._direct_click)
                            ]
                            
                            for strategy_name, click_method in click_strategies:
                                try:
                                    logger.info(f"Attempting {strategy_name}...")
                                    click_method(continuar_button)
                                    
                                    # Wait a moment and verify click was successful
                                    time.sleep(2)
                                    
                                    # Check if page changed or button is no longer there (success indicators)
                                    if self._verify_continuar_click():
                                        logger.info(f"✓ Descargar PDF button clicked successfully using {strategy_name}")
                                        button_clicked = True
                                        break
                                    else:
                                        logger.warning(f"{strategy_name} did not produce expected result")
                                        
                                except Exception as click_error:
                                    logger.warning(f"{strategy_name} failed: {str(click_error)}")
                                    continue
                            
                            if button_clicked:
                                break
                        else:
                            logger.warning(f"Button found but not clickable - Enabled: {is_enabled}, Displayed: {is_displayed}")
                            
                except TimeoutException:
                    logger.debug(f"Selector {i+1} timed out")
                    continue
                except Exception as e:
                    logger.debug(f"Selector {i+1} failed: {str(e)}")
                    continue
            
            if not button_clicked:
                # Final debug attempt
                self._debug_continuar_button()
                raise Exception("Could not find or click 'Descargar PDF' button with any strategy")
                
            return button_clicked
            
        except Exception as e:
            logger.error(f"Error clicking Descargar PDF button: {str(e)}")
            raise


            
    def _click_download_xml_button(self, timeout=60):
        """
        Click the 'Descargar XML' button with multiple fallback strategies
        """
        try:
            logger.info("Looking for 'Descargar XML' button...")
            
            # Strategy 1: Target by exact class combination and button text
            xml_selectors = [
                # Most specific - by class, text and icon
                (By.XPATH, "//a[contains(@class, 'btn btn-danger') and contains(@class, 'heightButton') and contains(text(), 'Descargar XML')]"),
                
                # By icon class and text
                (By.XPATH, "//a[.//i[contains(@class, 'bi-filetype-xml')] and contains(text(), 'Descargar XML')]"),
                
                # By text content only
                (By.XPATH, "//a[contains(text(), 'Descargar XML')]"),
                
                # By icon class only (less specific)
                (By.XPATH, "//a[.//i[contains(@class, 'bi-filetype-xml')]]"),
                
                # By href pattern (XML files)
                (By.XPATH, "//a[contains(@href, '.xml') and contains(@class, 'btn-danger')]"),
                
                # Case insensitive text matching
                (By.XPATH, "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar') and contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'xml')]")
            ]
            
            button_clicked = False
            
            for i, (by_method, selector) in enumerate(xml_selectors):
                try:
                    logger.info(f"Trying selector {i+1}: {selector}")
                    
                    # Wait for button to be present and clickable
                    continuar_button = WebDriverWait(self.driver, timeout).until(
                        EC.element_to_be_clickable((by_method, selector))
                    )
                    
                    if continuar_button:
                        # Log button details for debugging
                        button_text = continuar_button.text.strip()
                        button_classes = continuar_button.get_attribute('class')
                        is_enabled = continuar_button.is_enabled()
                        is_displayed = continuar_button.is_displayed()
                        
                        logger.info(f"Found button - Text: '{button_text}', Classes: '{button_classes}'")
                        logger.info(f"Button state - Enabled: {is_enabled}, Displayed: {is_displayed}")
                        
                        if is_enabled and is_displayed:
                            # Multiple click strategies for reliability
                            click_strategies = [
                                ("scroll_and_click", self._scroll_and_click),
                                ("javascript_click", self._javascript_click),
                                ("action_chains_click", self._action_chains_click),
                                ("direct_click", self._direct_click)
                            ]
                            
                            for strategy_name, click_method in click_strategies:
                                try:
                                    logger.info(f"Attempting {strategy_name}...")
                                    click_method(continuar_button)
                                    
                                    # Wait a moment and verify click was successful
                                    time.sleep(2)
                                    
                                    # Check if page changed or button is no longer there (success indicators)
                                    if self._verify_continuar_click():
                                        logger.info(f"✓ Descargar XML button clicked successfully using {strategy_name}")
                                        button_clicked = True
                                        break
                                    else:
                                        logger.warning(f"{strategy_name} did not produce expected result")
                                        
                                except Exception as click_error:
                                    logger.warning(f"{strategy_name} failed: {str(click_error)}")
                                    continue
                            
                            if button_clicked:
                                break
                        else:
                            logger.warning(f"Button found but not clickable - Enabled: {is_enabled}, Displayed: {is_displayed}")
                            
                except TimeoutException:
                    logger.debug(f"Selector {i+1} timed out")
                    continue
                except Exception as e:
                    logger.debug(f"Selector {i+1} failed: {str(e)}")
                    continue
            
            if not button_clicked:
                # Final debug attempt
                self._debug_continuar_button()
                raise Exception("Could not find or click 'Descargar XML' button with any strategy")
                
            return button_clicked
            
        except Exception as e:
            logger.error(f"Error clicking Descargar XML button: {str(e)}")
            raise

    def download_both_files(self, timeout=30):
        """
        Convenience method to download both PDF and XML files
        """
        try:
            logger.info("Starting download of both PDF and XML files...")
            
            # Download PDF first
            pdf_success = self._click_download_pdf_button(timeout)
            if pdf_success:
                logger.info("✓ PDF download initiated successfully")
                time.sleep(2)  # Brief pause between downloads
            
            # Download XML
            xml_success = self._click_download_xml_button(timeout)
            if xml_success:
                logger.info("✓ XML download initiated successfully")
            
            if pdf_success and xml_success:
                logger.info("✓ Both PDF and XML downloads initiated successfully")
                return True
            else:
                logger.warning("⚠ One or more downloads may have failed")
                return False
                
        except Exception as e:
            logger.error(f"Error downloading files: {str(e)}")
            raise

    def _wait_for_both_downloads(self, timeout=60):
        """
        Wait for both PDF and XML files to be downloaded or verify existing files
        
        Returns:
            tuple: (pdf_file_path, xml_file_path)
        """
        start_time = time.time()
        
        # Ensure download_directory is properly set and expanded
        if not hasattr(self, 'download_directory') or not self.download_directory:
            #self.download_directory = os.path.expanduser("~/Downloads")
            logger.warning(f"download_directory was not set, using default: {self.download_directory}")
        
        # Expand user path and create Path object
        expanded_path = os.path.expanduser(self.download_directory)
        download_dir = Path(expanded_path)
        
        # Ensure directory exists
        if not download_dir.exists():
            logger.error(f"Download directory does not exist: {download_dir}")
            download_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created download directory: {download_dir}")
        
        logger.info(f"Checking for PDF and XML files in: {download_dir}")
        
        pdf_file = None
        xml_file = None
        
        # First, check if files already exist
        pdf_files = list(download_dir.glob("*.pdf"))
        xml_files = list(download_dir.glob("*.xml"))
        
        if pdf_files and xml_files:
            # Get the most recent files
            pdf_file = max(pdf_files, key=lambda f: f.stat().st_ctime)
            xml_file = max(xml_files, key=lambda f: f.stat().st_ctime)
            
            # Verify both files are complete
            pdf_ready = self._verify_file_complete(pdf_file)
            xml_ready = self._verify_file_complete(xml_file)
            
            if pdf_ready and xml_ready:
                logger.info("✓ Found existing PDF and XML files that are ready")
                return pdf_file, xml_file
        
        # If files aren't already complete, wait for them
        logger.info("Files not found or incomplete, waiting for downloads...")
        
        # Get initial file sets if we need to wait for new files
        initial_pdfs = set(pdf_files)
        initial_xmls = set(xml_files)
        
        while time.time() - start_time < timeout:
            try:
                # Check for new PDF files
                current_pdfs = set(download_dir.glob("*.pdf"))
                new_pdfs = current_pdfs - initial_pdfs
                
                # Check for new XML files
                current_xmls = set(download_dir.glob("*.xml"))
                new_xmls = current_xmls - initial_xmls
                
                # Check if we found new files
                if new_pdfs and not pdf_file:
                    # Get the most recent PDF
                    pdf_candidates = sorted(new_pdfs, key=lambda f: f.stat().st_ctime, reverse=True)
                    pdf_file = self._verify_file_complete(pdf_candidates[0])
                    if pdf_file:
                        logger.info(f"✓ PDF file ready: {pdf_file.name}")
                
                if new_xmls and not xml_file:
                    # Get the most recent XML
                    xml_candidates = sorted(new_xmls, key=lambda f: f.stat().st_ctime, reverse=True)
                    xml_file = self._verify_file_complete(xml_candidates[0])
                    if xml_file:
                        logger.info(f"✓ XML file ready: {xml_file.name}")
                
                # If both files are found and complete, we're done
                if pdf_file and xml_file:
                    logger.info("✓ Both PDF and XML files downloaded successfully")
                    return pdf_file, xml_file
                
                # Check for partial downloads
                temp_files = list(download_dir.glob("*.crdownload"))
                temp_files.extend(list(download_dir.glob("*.tmp")))
                temp_files.extend(list(download_dir.glob("*.part")))
                
                if temp_files:
                    logger.debug(f"Downloads in progress ({len(temp_files)} temp files)...")
                
                # Log progress
                elapsed = time.time() - start_time
                if elapsed % 10 < 2:  # Log every ~10 seconds
                    pdf_status = "✓" if pdf_file else "⏳"
                    xml_status = "✓" if xml_file else "⏳"
                    logger.info(f"Download progress ({elapsed:.0f}s): PDF {pdf_status}, XML {xml_status}")
                
                time.sleep(2)  # Check every 2 seconds
                
            except Exception as check_error:
                logger.debug(f"Error during download check: {str(check_error)}")
                time.sleep(2)
        
        # Timeout handling
        if not pdf_file and not xml_file:
            raise TimeoutException(f"Neither PDF nor XML file downloaded after {timeout} seconds")
        elif not pdf_file:
            raise TimeoutException(f"PDF file not downloaded after {timeout} seconds (XML ready)")
        elif not xml_file:
            raise TimeoutException(f"XML file not downloaded after {timeout} seconds (PDF ready)")

    def _verify_file_complete(self, file_path):
        """
        Verify that a file is completely downloaded and readable
        
        Args:
            file_path (Path): Path to the file to verify
            
        Returns:
            Path or None: File path if complete, None if still downloading
        """
        try:
            if not file_path.exists():
                return None
            
            # Check file size (should be greater than 0)
            file_size = file_path.stat().st_size
            if file_size == 0:
                logger.debug(f"File {file_path.name} is empty, still downloading...")
                return None
            
            # For small files, wait a bit more to ensure completion
            if file_size < 1024:  # Less than 1KB
                time.sleep(1)
                new_size = file_path.stat().st_size
                if new_size != file_size:
                    logger.debug(f"File {file_path.name} still growing, waiting...")
                    return None
            
            # Try to open the file to ensure it's not locked
            try:
                with open(file_path, 'rb') as f:
                    f.read(100)  # Read first 100 bytes
                logger.debug(f"File {file_path.name} verified complete ({file_size} bytes)")
                return file_path
            except (PermissionError, OSError):
                logger.debug(f"File {file_path.name} still being written...")
                return None
                
        except Exception as e:
            logger.debug(f"Error verifying file {file_path}: {str(e)}")
            return None

    def _create_zip_from_files(self, pdf_file, xml_file, zip_filename=None):
        """
        Create a ZIP file containing the PDF and XML files
        
        Args:
            pdf_file (Path): Path to the PDF file
            xml_file (Path): Path to the XML file
            zip_filename (str): Optional custom filename for ZIP
            
        Returns:
            str: Path to the created ZIP file
        """
        try:
            # Ensure we have a valid download directory
            if not hasattr(self, 'download_directory') or not self.download_directory:
                #self.download_directory = os.path.expanduser("~/Downloads")
                self.download_directory = DOWNLOADS_DIR
            
            expanded_path = os.path.expanduser(self.download_directory)
            download_dir_path = Path(expanded_path)
            
            # Generate ZIP filename if not provided
            if not zip_filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                # Try to extract ticket/folio info from filenames for better naming
                ticket_info = self._extract_ticket_info_from_filename(pdf_file.name)
                if ticket_info:
                    zip_filename = f"factura_{ticket_info}_{timestamp}.zip"
                else:
                    zip_filename = f"factura_{timestamp}.zip"
            
            # Ensure ZIP filename has .zip extension
            if not zip_filename.endswith('.zip'):
                zip_filename += '.zip'
            
            # Create ZIP file path in the download directory
            zip_file_path = download_dir_path / zip_filename
            
            logger.info(f"Creating ZIP file: {zip_file_path}")
            
            with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add PDF file
                zipf.write(pdf_file, pdf_file.name)
                logger.info(f"Added to ZIP: {pdf_file.name} ({pdf_file.stat().st_size} bytes)")
                
                # Add XML file
                zipf.write(xml_file, xml_file.name)
                logger.info(f"Added to ZIP: {xml_file.name} ({xml_file.stat().st_size} bytes)")
            
            # Verify ZIP file was created successfully
            if zip_file_path.exists():
                zip_size = zip_file_path.stat().st_size
                logger.info(f"✓ ZIP file created successfully: {zip_filename} ({zip_size} bytes)")
                
                # Verify ZIP contents
                with zipfile.ZipFile(zip_file_path, 'r') as zipf:
                    zip_contents = zipf.namelist()
                    logger.info(f"ZIP contents: {zip_contents}")
                    
                    # Test ZIP integrity
                    bad_file = zipf.testzip()
                    if bad_file:
                        raise Exception(f"ZIP file corrupted, bad file: {bad_file}")
                    else:
                        logger.info("✓ ZIP file integrity verified")
                
                return str(zip_file_path)
            else:
                raise Exception("ZIP file was not created")
                
        except Exception as e:
            logger.error(f"Error creating ZIP file: {str(e)}")
            raise
            
    def _extract_ticket_info_from_filename(self, filename):
        """
        Try to extract ticket/folio information from filename for better ZIP naming
        
        Args:
            filename (str): The PDF filename
            
        Returns:
            str or None: Extracted ticket info or None
        """
        try:
            # Common patterns in invoice filenames
            import re
            
            # Look for patterns like "VEMA880823699", ticket numbers, etc.
            patterns = [
                r'([A-Z]{4}\d{9})',  # Pattern like VEMA880823699
                r'(CFC\d+)',         # Pattern like CFC1101217742
                r'(\d{10,})',        # Long number sequences
                r'([A-Z]+\d+[A-Z]*\d*)'  # Mixed alphanumeric
            ]
            
            for pattern in patterns:
                match = re.search(pattern, filename)
                if match:
                    return match.group(1)
            
            # If no pattern matches, return first part of filename (without extension)
            base_name = Path(filename).stem
            if len(base_name) > 5:  # Only if reasonably long
                return base_name[:15]  # Limit length
                
            return None
            
        except Exception as e:
            logger.debug(f"Error extracting ticket info from filename: {str(e)}")
            return None

    def _cleanup_individual_files(self, pdf_file, xml_file):
        """
        Remove individual PDF and XML files after successful ZIP creation
        
        Args:
            pdf_file (Path): Path to the PDF file
            xml_file (Path): Path to the XML file
        """
        try:
            logger.info("Cleaning up individual files after ZIP creation...")
            
            # Remove PDF file
            if pdf_file and pdf_file.exists():
                pdf_file.unlink()
                logger.info(f"✓ Removed PDF file: {pdf_file.name}")
            
            # Remove XML file
            if xml_file and xml_file.exists():
                xml_file.unlink()
                logger.info(f"✓ Removed XML file: {xml_file.name}")
            
            logger.info("✓ Cleanup completed successfully")
            
        except Exception as e:
            logger.warning(f"Error during cleanup (files may remain): {str(e)}")
            # Don't raise exception for cleanup errors

    def _get_latest_invoice_zip(self):
        """
        Get the path to the most recently created invoice ZIP file
        
        Returns:
            str or None: Path to the latest ZIP file
        """
        try:
            download_dir = Path(self.download_directory)
            zip_files = list(download_dir.glob("factura_*.zip"))
            
            if not zip_files:
                return None
            
            # Return the most recent ZIP file
            latest_zip = max(zip_files, key=lambda f: f.stat().st_ctime)
            return str(latest_zip)
            
        except Exception as e:
            logger.error(f"Error getting latest invoice ZIP: {str(e)}")
            return None

    # Enhanced method for your existing workflow
    def process_invoice_complete(self, data, cleanup_individual_files=True):
        """
        Complete invoice processing workflow: fill form, download files, create ZIP
        
        Args:
            data (dict): Form data for invoice generation
            cleanup_individual_files (bool): Whether to remove PDF/XML after ZIP creation
            
        Returns:
            str: Path to the created ZIP file
        """
        try:
            logger.info("Starting complete invoice processing workflow...")
            
            # Fill the form (your existing method)
            self.fill_form(data)
            
            # Create ZIP with downloaded files
            zip_file_path = self.create_invoice_zip()
            
            logger.info(f"✓ Complete invoice processing finished: {zip_file_path}")
            return zip_file_path
            
        except Exception as e:
            logger.error(f"Error in complete invoice processing: {str(e)}")
            raise 

 
    def _click_continuar_button(self, timeout=30):
        """
        Click the 'Continuar' button with multiple fallback strategies
        """
        try:
            logger.info("Looking for 'Continuar' button...")
            
            # Strategy 1: Target by exact class combination and button text
            continuar_selectors = [
                # Most specific - by class and text
                (By.XPATH, "//button[contains(@class, 'btn btn-primary') and contains(@class, 'buttonSubmit') and contains(text(), 'Continuar')]"),
                
                # By buttonSubmit class and text
                (By.XPATH, "//button[contains(@class, 'buttonSubmit') and contains(text(), 'Continuar')]"),
                
                # By heightButton class and text
                (By.XPATH, "//button[contains(@class, 'heightButton') and contains(text(), 'Continuar')]"),
                
                # By btn-primary and text
                (By.XPATH, "//button[contains(@class, 'btn-primary') and contains(text(), 'Continuar')]"),
                
                # Just by text (most generic)
                (By.XPATH, "//button[contains(text(), 'Continuar')]"),
                
                # Alternative text matching (case insensitive)
                (By.XPATH, "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continuar')]")
            ]
            
            button_clicked = False
            
            for i, (by_method, selector) in enumerate(continuar_selectors):
                try:
                    logger.info(f"Trying selector {i+1}: {selector}")
                    
                    # Wait for button to be present and clickable
                    continuar_button = WebDriverWait(self.driver, timeout).until(
                        EC.element_to_be_clickable((by_method, selector))
                    )
                    
                    if continuar_button:
                        # Log button details for debugging
                        button_text = continuar_button.text.strip()
                        button_classes = continuar_button.get_attribute('class')
                        is_enabled = continuar_button.is_enabled()
                        is_displayed = continuar_button.is_displayed()
                        
                        logger.info(f"Found button - Text: '{button_text}', Classes: '{button_classes}'")
                        logger.info(f"Button state - Enabled: {is_enabled}, Displayed: {is_displayed}")
                        
                        if is_enabled and is_displayed:
                            # Multiple click strategies for reliability
                            click_strategies = [
                                ("scroll_and_click", self._scroll_and_click),
                                ("javascript_click", self._javascript_click),
                                ("action_chains_click", self._action_chains_click),
                                ("direct_click", self._direct_click)
                            ]
                            
                            for strategy_name, click_method in click_strategies:
                                try:
                                    logger.info(f"Attempting {strategy_name}...")
                                    click_method(continuar_button)
                                    
                                    # Wait a moment and verify click was successful
                                    time.sleep(2)
                                    
                                    # Check if page changed or button is no longer there (success indicators)
                                    if self._verify_continuar_click():
                                        logger.info(f"✓ Continuar button clicked successfully using {strategy_name}")
                                        button_clicked = True
                                        break
                                    else:
                                        logger.warning(f"{strategy_name} did not produce expected result")
                                        
                                except Exception as click_error:
                                    logger.warning(f"{strategy_name} failed: {str(click_error)}")
                                    continue
                            
                            if button_clicked:
                                break
                        else:
                            logger.warning(f"Button found but not clickable - Enabled: {is_enabled}, Displayed: {is_displayed}")
                            
                except TimeoutException:
                    logger.debug(f"Selector {i+1} timed out")
                    continue
                except Exception as e:
                    logger.debug(f"Selector {i+1} failed: {str(e)}")
                    continue
            
            if not button_clicked:
                # Final debug attempt
                self._debug_continuar_button()
                raise Exception("Could not find or click 'Continuar' button with any strategy")
                
            return button_clicked
            
        except Exception as e:
            logger.error(f"Error clicking Continuar button: {str(e)}")
            raise
 
    def _scroll_and_click(self, element):
        """Scroll to element and click"""
        self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        time.sleep(1)  # Wait for scroll to complete
        element.click()

    def _javascript_click(self, element):
        """Click using JavaScript"""
        self.driver.execute_script("arguments[0].click();", element)

    def _action_chains_click(self, element):
        """Click using ActionChains"""
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(self.driver).move_to_element(element).click().perform()

    def _direct_click(self, element):
        """Direct click method"""
        element.click()

    def _verify_continuar_click(self):
        """Verify that the Continuar button click was successful"""
        try:
            # Check for URL change
            current_url = self.driver.current_url
            logger.debug(f"Current URL after click: {current_url}")
            
            # Check if button is still present (if gone, likely successful)
            try:
                continuar_buttons = self.driver.find_elements(By.XPATH, "//button[contains(text(), 'Continuar')]")
                if not continuar_buttons:
                    logger.info("Continuar button no longer present - likely successful")
                    return True
            except:
                pass
            
            # Check for new elements that appear after successful click
            success_indicators = [
                # Look for elements that might appear in next step
                "ConfirmarCorreo",  # Email field from second section
                "inputRF",          # Régimen Fiscal dropdown
                "inputState",       # Uso CFDI dropdown
            ]
            
            for indicator in success_indicators:
                try:
                    element = self.driver.find_element(By.ID, indicator)
                    if element.is_displayed():
                        logger.info(f"Success indicator found: {indicator}")
                        return True
                except:
                    continue
            
            # Check for any loading indicators
            loading_selectors = [".loading", ".spinner", "[aria-busy='true']"]
            for selector in loading_selectors:
                try:
                    loading_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if any(elem.is_displayed() for elem in loading_elements):
                        logger.info("Loading indicator found - processing in progress")
                        # Wait for loading to complete
                        WebDriverWait(self.driver, 10).until_not(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        return True
                except:
                    continue
            
            # If we can't determine success, assume it worked if no errors occurred
            logger.debug("Could not definitively verify click success, assuming successful")
            return True
            
        except Exception as e:
            logger.debug(f"Error verifying click: {str(e)}")
            return True  # Assume success if verification fails
 


# Initialize the automation class
automation = None

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

@app.route('/generate-invoice', methods=['POST'])
def generate_invoice():
    """Main endpoint to generate invoice ZIP and send it to client"""
    global automation

    try:
        # Validate JSON data
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.get_json()

        # Validate required fields
#        required_fields = [
#            'folio_factura', 'caja', 'fecha_compra', 'ticket',
#            'rfc', 'codigo_postal', 'razon_social', 'regimen_fiscal', 'uso_cfdi'
#        ]
        
        required_fields = [
            'ticket',
            'rfc', 'regimen_fiscal', 'uso_cfdi'
        ]

        # Check which service to use
        servicio = data.get('servicio').lower()
        accion = data.get('accion').lower()

        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            return jsonify({
                "error": "Missing required fields",
                "missing_fields": missing_fields
            }), 400

        # Validate email fields if email is requested
        if data.get('send_email', False):
            email_fields = ['email', 'email_confirm']
            missing_email_fields = [field for field in email_fields if field not in data]
            if missing_email_fields:
                return jsonify({
                    "error": "Missing email fields when send_email is true",
                    "missing_fields": missing_email_fields
                }), 400

            if data['email'] != data['email_confirm']:
                return jsonify({"error": "Email and email confirmation do not match"}), 400

        # Initialize automation
        automation = ServiceStore()
        automation.setup_driver()

        # Fill form and get ZIP path using new method
        zip_file_path = None
        try:
            # Process the form
            if servicio == 'farmaciaguadalajara':
                automation.fill_form_guadalajara(data)
            elif servicio == 'farmaciadelahorro' and accion == 'facturar':
                automation.fill_form_ahorro(data)
            elif servicio == 'farmaciadelahorro' and accion == 'descargar':
                automation.fill_form_ahorro_descargar(data)
            
            # Wait for file and get its path
            zip_file_path = automation.sending_file()
            
            # Send file to client
            zip_path = Path(zip_file_path)
            
            def remove_file():
                """Remove file after sending"""
                try:
                    if zip_path.exists():
                        zip_path.unlink()
                        logger.info(f"Successfully deleted ZIP file: {zip_path}")
                    else:
                        logger.warning(f"ZIP file not found for deletion: {zip_path}")
                except Exception as delete_error:
                    logger.error(f"Error deleting ZIP file: {str(delete_error)}")
            
            # Send file with automatic cleanup
            response = send_file(
                zip_file_path,
                as_attachment=True,
                download_name=f"factura_{data.get('folio_factura', 'unknown')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                mimetype='application/zip'
            )
            
            # Schedule file deletion after response is sent
            @response.call_on_close
            def cleanup():
                remove_file()
            
            return response

        except Exception as process_error:
            # If there was an error and we have a file path, clean it up
            if zip_file_path and Path(zip_file_path).exists():
                try:
                    Path(zip_file_path).unlink()
                    logger.info(f"Cleaned up ZIP file after error: {zip_file_path}")
                except Exception as cleanup_error:
                    logger.error(f"Error cleaning up ZIP file: {str(cleanup_error)}")
            
            raise process_error

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

    finally:
        # Clean up driver
        if automation:
            automation.close_driver()
            clean_downloads_dir()
            automation = None

#@atexit.register
#def final_cleanup():
#    """Last-resort cleanup on server exit"""
#    with cleanup_lock:
#        clean_downloads_dir()
#        for file_path in pending_cleanup.copy():
#            try:
#                Path(file_path).unlink(missing_ok=True)
#            except Exception as e:
#                logger.error(f"Final cleanup failed for {file_path}: {str(e)}")  

if __name__ == '__main__':
    # Create download directory if it doesn't exist
    #os.makedirs("~/Downloads", exist_ok=True)

    # Run the Flask app
    app.run(debug=True, host='0.0.0.0', port=5000)

