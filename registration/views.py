import asyncio
import logging
import os
import pdb
import time
import random
import re
import requests
from logging.handlers import RotatingFileHandler
from django.http import JsonResponse
from rest_framework.views import APIView
from .serializers import RegisterSerializer
from playwright.async_api import async_playwright
from fake_useragent import UserAgent
from faker import Faker
from skimage import io, transform
from stem import Signal
from stem.control import Controller
from decouple import config
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import BasicAuthentication
from asgiref.sync import async_to_sync, sync_to_async

# Setting up logging
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(level=logging.INFO)
handler = RotatingFileHandler(
    "logs/facebook_registration.log", maxBytes=2000, backupCount=10
)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.addHandler(handler)

fake = Faker()

# List of random locations
locations = [
    {"latitude": 37.7749, "longitude": -122.4194},  # San Francisco, CA
    {"latitude": 40.7128, "longitude": -74.0060},  # New York, NY
    {"latitude": 51.5074, "longitude": -0.1278},  # London, UK
    {"latitude": 48.8566, "longitude": 2.3522},  # Paris, France
    {"latitude": 35.6895, "longitude": 139.6917},  # Tokyo, Japan
]


def renew_tor_connection():
    """
    Renews the TOR connection by sending a NEWNYM signal to the TOR controller.

    This function connects to the TOR controller on the specified port,
    authenticates using the provided password, and sends a NEWNYM signal
    to request a new TOR circuit. If the operation is successful,
    it logs a success message. If an error occurs, it logs the exception.

    Raises:
    Exception: If there is an error renewing the TOR connection,
               the exception is logged.

    Example:
    >>> renew_tor_connection()
    """
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate(password=config("TOR_PASSWORD"))
            controller.signal(Signal.NEWNYM)
            logger.info("TOR connection renewed successfully")
    except Exception as e:
        logger.exception(f"Error renewing TOR connection: {str(e)}")


async def connection(temp_email, sid_token, browser_type):
    """
    Establishes a connection using Tor, opens the Facebook registration page, and handles the cookies banner.

    Args:
        temp_email (str): Temporary email to use for the registration.
        sid_token (str): SID token for session management.

    Returns:
        bool: True if the process is successful, False otherwise.
    """
    logger.info("Start Functions: connection")
    renew_tor_connection()
    user_agent = UserAgent().random
    location = random.choice(locations)

    async with async_playwright() as p:
        if browser_type == "firefox":
            browser = await p.firefox.launch(
                proxy={"server": "socks5://127.0.0.1:9050"}, headless=False
            )
        elif browser_type == "chromium":
            browser = await p.chromium.launch(
                proxy={"server": "socks5://127.0.0.1:9050"}, headless=False
            )
        else:  # По умолчанию используем webkit
            browser = await p.webkit.launch(
                proxy={"server": "socks5://127.0.0.1:9050"}, headless=False
            )
        context = await browser.new_context(
            user_agent=user_agent,
            geolocation=location,
            permissions=["geolocation"],
            locale="en-US",
        )
        page = await context.new_page()

        logger.info(f"Opening Facebook registration page for {temp_email}")
        try:
            await page.goto("https://www.facebook.com/r.php", wait_until="networkidle")
            logger.info("Facebook registration page opened successfully")
        except Exception as e:
            logger.exception("Failed to open Facebook registration page")
            await browser.close()
            return False

        await page.wait_for_timeout(10000)  # Wait a bit after the page loads
        try:
            return await handle_cookies_banner(page, browser, temp_email, sid_token)
        except Exception as e:
            logger.exception(f"Error: {e}")
            await browser.close()
            return False


# Function to simulate typing delay and mouse movement
async def human_typing(page, selector: str, text: str):
    """Simulates human-like typing into a specified input field."""
    logger.info(f"Typing text: {text} into {selector}")
    element = await page.query_selector(selector)
    if element:
        box = await element.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
            await page.wait_for_timeout(random.randint(500, 1000))
        await page.fill(selector, text)
        await page.wait_for_timeout(random.randint(500, 2000))


async def handle_confirmation_code(
    page, selector_input, selector_button, sid_token, timeout=30
):
    """
    Handles the input of the confirmation code on the page.

    This function waits for the confirmation code input field to become visible,
    retrieves the confirmation code using the `get_confirmation_code` function,
    types it into the specified field, and clicks the confirmation button.

    Parameters:
    page (Page): The page object where the action is performed.
    selector_input (str): The CSS selector for the confirmation code input field.
    selector_button (str): The CSS selector for the confirmation button.
    sid_token (str): The session token for authentication.

    Returns:
    bool: True if the confirmation code was successfully entered and the button was clicked,
          otherwise False.
    """
    try:
        await page.wait_for_selector(
            selector_input, timeout=timeout
        )  # Reduced timeout for better responsiveness
        logger.info("Waiting for confirmation code")
        await page.wait_for_timeout(20000)
        confirmation_code = get_confirmation_code(sid_token)

        if confirmation_code:
            logger.info(f"Confirmation code received: {confirmation_code}")
            await human_typing(page, selector_input, confirmation_code)
            await page.click(selector_button)
            logger.info(f"Clicked Confirmation code  successfully.")
            return True

        logger.info("Verification code not received")
        return False
    except TimeoutError as e:
        logger.error(f"The waiting time has expired {selector_input}")
    except Exception as e:
        logger.exception(f"Error in handle_confirmation_code: {e}")
        return False


def get_confirmation_code(sid_token):
    """
    Retrieves the confirmation code from Guerrilla Mail using the provided session ID token.

    This function waits for a brief period to allow time for the email containing the confirmation code to arrive.
    It then makes a request to the Guerrilla Mail API to check for new emails. If an email is found, the function
    extracts the confirmation code from the email excerpt and returns it.

    Args:
        sid_token (str): The session ID token used to authenticate the request with Guerrilla Mail.

    Returns:
        str or None: The confirmation code extracted from the email, or None if no code is found or an error occurs.

    Raises:
        requests.exceptions.HTTPError: If an HTTP error occurs during the request.
        requests.exceptions.RequestException: For general request-related errors.
        requests.exceptions.JSONDecodeError: If there is an error decoding the JSON response.
        KeyError: If the expected key is not found in the JSON response.

    Example:
        >>> sid_token = "your_sid_token"
        >>> code = get_confirmation_code(sid_token)
        >>> print(code)
        "123456"

    Logs:
        - Logs an info message when the confirmation code is found.
        - Logs an info message if no confirmation code is found or if no emails are found.
        - Logs exception details for HTTP errors, request exceptions, JSON decoding errors, and key errors.
    """
    try:
        time.sleep(25)  # We wait for some time for the letter to arrive.

        response = requests.get(
            f"https://api.guerrillamail.com/ajax.php?f=check_email&seq=0&sid_token={sid_token}"
        )

        response.raise_for_status()  # Checking that the request completed successfully

        email_data = response.json()
        emails = email_data.get("list")

        if emails:
            confirmation_message = emails[0].get("mail_subject")
            if confirmation_message:
                confirmation_code = re.search(r"\d+", confirmation_message).group()
                logger.info("Confirmation code found.")
                return confirmation_code
            else:
                logger.info("No confirmation code found in the email excerpt.")
                return None
        else:
            logger.info("No emails found.")
            return None

    except requests.exceptions.HTTPError as e:
        logger.exception(f"HTTP error occurred: {e}")
    except requests.exceptions.RequestException as e:
        logger.exception(f"Request exception: {e}")
    except requests.exceptions.JSONDecodeError as e:
        logger.exception(f"JSON decode error: {e}")
        logger.exception(
            f"Response text: {response.text if response else 'No response'}"
        )
    except KeyError as e:
        logger.exception(f"Key error: {e}")
    except Exception as e:
        logger.exception(f"An error occurred: {e}")

    return None


async def close_cookies_banner(page, browser, temp_email, sid_token):
    """
    Close the cookies banner on the page if it is visible.

    This function attempts to locate and click the "Allow all cookies" button on a web page.
    It simulates human-like mouse movements and adds a small delay before clicking the button.

    Args:
        page (playwright.async_api.Page): The Playwright page object.

    Returns:
        None

    Raises:
        TimeoutError: If there is a timeout while trying to close the cookies banner.
        Exception: If any other error occurs during the process.
    """
    try:
        # Search for the element with role "button" and name "Allow all cookies"
        button = page.get_by_role("button", name="Allow all cookies")

        if await button.is_visible():
            # Get the coordinates and dimensions of the element
            box = await button.bounding_box()
            if box:
                # Simulate moving the mouse to the center of the element
                await page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await page.wait_for_timeout(500)  # Pause to simulate delay

                # Simulate clicking the element
                await button.click()
                logger.info("Cookies banner found and closed.")
                await register_facebook_account(page, browser, temp_email, sid_token)
            else:
                logger.info("Bounding box not found for cookies button.")
        else:
            logger.info("No cookies banner found.")
    except TimeoutError as e:
        logger.exception(f"Timeout error closing cookies banner: {e}")
    except Exception as e:
        logger.exception(f"Error closing cookies banner: {e}")


async def click_button(page, button, name):
    """Clicks a button and logs the action, returning False if the button is not visible."""
    if await button.is_visible():
        box = await button.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
            await page.wait_for_timeout(500)  # Pause to simulate delay
            await button.click()
            logger.info(f"Clicked on button {name}.")
            return True
    logger.info(f"Button {name} not found or not visible.")
    return False


async def handle_error(page, temp_email):
    """Handles errors during registration and takes a screenshot."""
    try:
        if page and not page.is_closed():
            screenshot_path = f"logs/screenshot_{temp_email}.png"
            await page.screenshot(path=screenshot_path)
            logger.exception(f"Screenshot saved to {screenshot_path}")
    except Exception as screenshot_error:
        logger.exception("Failed to take screenshot")


async def register_facebook_account_v2(page, browser, temp_email, sid_token) -> bool:
    """
    Registers a Facebook account using the provided page and browser instances.

    This asynchronous function fills out the Facebook registration form using
    randomly generated names, a date of birth, gender, email, and password.
    It simulates human-like interactions such as typing and mouse movements.

    Args:
        page (Page): The Playwright page instance to interact with.
        browser (Browser): The Playwright browser instance to close if needed.
        temp_email (str): The temporary email address to use for registration.
        sid_token (str): The session ID token (not used in the current implementation).

    Returns:
        bool: True if the registration was successful, False otherwise.
    """
    logger.info("Function start: register_facebook_account_v2")
    try:
        try:
            await page.wait_for_selector(
                'input[aria-label="First name"]', timeout=120000
            )
            logger.info("Registration form loaded. First name input found.")
        except TimeoutError:
            logger.exception("Timeout while waiting for registration form.")
            await page.screenshot(path="logs/form_load_timeout.png")
            await browser.close()
            return False

        await human_typing(page, 'input[aria-label="First name"]', fake.first_name())
        await human_typing(page, 'input[aria-label="Last name"]', fake.last_name())

        next_button = page.get_by_role("button", name="Next")
        if await click_button(page, next_button, "Next"):
            await page.wait_for_timeout(3000)  # Wait for the next page to load

            # Fill in the date of birth
            birth_date = fake.date_of_birth(minimum_age=18, maximum_age=100)
            birth_date_str = birth_date.strftime("%Y-%m-%d")
            await page.fill('input[type="date"]', birth_date_str)
            logger.info(f"Filled date of birth: {birth_date_str}")

            if await click_button(page, next_button, "Next"):
                await page.wait_for_timeout(7000)  # Wait for the next page to load

                # Select gender
                gender = random.choice(["Female", "Male"])
                await page.click(f'div[role="radio"][aria-label="{gender}"]')
                logger.info(f"Selected gender: {gender}")

                if await click_button(page, next_button, "Next"):
                    await page.wait_for_timeout(10000)  # Wait for the next page to load

                    # Sign up with email
                    sign_up_email_button = page.get_by_role(
                        "button", name="Sign up with email"
                    )
                    if await click_button(
                        page, sign_up_email_button, "Sign up with email"
                    ):
                        # Fill in the email
                        email_input = await page.wait_for_selector(
                            'input[aria-label="Email"]', timeout=10000
                        )
                        if email_input:
                            # await email_input.fill(temp_email)
                            await human_typing(
                                page, 'input[aria-label="Email"]', temp_email
                            )
                            logger.info(f"Filled email: {temp_email}")
                            if await click_button(page, next_button, "Next"):
                                # Fill in the password
                                await page.wait_for_selector(
                                    'input[aria-label="Password"]', timeout=10000
                                )
                                logger.info("Password input found")
                                await human_typing(
                                    page,
                                    'input[aria-label="Password"]',
                                    fake.password(),
                                )
                                if await click_button(page, next_button, "Next"):
                                    # Click the Save button
                                    if await click_button(
                                        page,
                                        await page.wait_for_selector(
                                            'div[role="button"][aria-label="Save"]'
                                        ),
                                        "Save",
                                    ):
                                        # Click the I agree button

                                        if await click_button(
                                            page,
                                            await page.wait_for_selector(
                                                'div[role="button"][aria-label="I agree"]'
                                            ),
                                            "I agree",
                                        ):
                                            if await handle_confirmation_code(
                                                page,
                                                'input[aria-label="Confirmation code"]',
                                                'div[role="button"][aria-label="Next"]',
                                                sid_token,
                                                timeout=45000,
                                            ):
                                                logger.info(
                                                    f"Registration successful: {temp_email}."
                                                )
                                                await browser.close()
                                                return True

                                            try:
                                                continue_button = await page.wait_for_selector(
                                                    'button[type="submit"][value="Continue"]',
                                                    timeout=45000,
                                                )
                                                await continue_button.click()
                                                logger.info(
                                                    f"Successfully registered: {temp_email}"
                                                )
                                                await browser.close()

                                                return True
                                            except TimeoutError:
                                                logger.error(
                                                    "Timeout while waiting for confirmation code input field."
                                                )
                                                await page.screenshot(
                                                    path=f"logs/screenshot_{temp_email}.png"
                                                )

                                                await browser.close()
                                                logger.info(
                                                    f"Registration not successful: {temp_email}"
                                                )
                                                return False

    except Exception as e:
        logger.exception(f"Error during registration {e}")
        await handle_error(page, temp_email)

        await browser.close()
        return False


async def get_started_button(page, browser, temp_email, sid_token):
    """
    Close the 'Get started' banner on the page if it is visible.

    This function attempts to locate and click the "Get started" button on a web page.
    It simulates human-like mouse movements and adds a small delay before clicking the button.

    Args:
        page (playwright.async_api.Page): The Playwright page object.

    Returns:
        None

    Raises:
        TimeoutError: If there is a timeout while trying to close the 'Get started' banner.
        Exception: If any other error occurs during the process.
    """
    logger.info("Attempting to close 'Get started' banner if visible.")
    try:
        # Search for the element with role "button" and name "Get started"
        button = page.get_by_role("button", name="Get started")

        if await click_button(page, button, "Get started"):
            logger.info("Clicked 'Get started' button successfully.")
            return await register_facebook_account_v2(
                page, browser, temp_email, sid_token
            )
        else:
            logger.info("Get started banner not found.")
            return
    except TimeoutError as e:
        logger.exception(f"Timeout error closing Get started banner: {e}")
    except Exception as e:
        logger.exception(f"Error closing Get started banner: {e}")


async def handle_cookies_banner(page, browser, temp_email, sid_token):
    """
    Handles the cookies banner on the page.

    This function searches for buttons to allow the use of cookies and
    performs the corresponding actions based on which button is clicked.
    If the "Allow all cookies" button is found, it will be clicked,
    and further actions such as registering a Facebook account will be awaited.

    Parameters:
    page (Page): The page object where the action is performed.
    browser (Browser): The browser object used to manage the session.
    temp_email (str): Temporary email address for registration.
    sid_token (str): Session token for authentication.

    Returns:
    Coroutine: The result of the account registration function.
    """
    logger.info("Start Functions: handle_cookies_banner")
    try:
        # Search for the element with the title "Allow all cookies"
        button_title = page.get_by_title("Allow all cookies")
        if await click_button(page, button_title, "Allow all cookies"):
            logger.info(f"Clicked 'Allow all cookies' {button_title} successfully.")
            await page.wait_for_timeout(5000)
            return await get_started_button(page, browser, temp_email, sid_token)

        button_cookies = page.get_by_role("button", name="Allow all cookies")
        if await click_button(page, button_cookies, "Allow all cookies"):
            logger.info(f"Clicked 'Allow all cookies' {button_cookies} successfully.")
            await page.wait_for_timeout(7000)
            return await register_facebook_account(page, browser, temp_email, sid_token)

        button_get_started = page.get_by_role("button", name="Get started")
        if await click_button(page, button_get_started, "Get started"):
            logger.info(
                f"Clicked 'Allow all cookies' {button_get_started} successfully."
            )
            return await register_facebook_account_v2(
                page, browser, temp_email, sid_token
            )

        return await register_facebook_account(page, browser, temp_email, sid_token)
    except TimeoutError as e:
        logger.exception(
            f"handle_cookies_banner - Timeout error closing cookies banner: {e}"
        )
    except Exception as e:
        logger.exception(f"handle_cookies_banner - Error closing cookies banner: {e}")


async def register_facebook_account(page, browser, temp_email, sid_token):
    """
    Register a new Facebook account using a temporary email and SID token.

    This function automates the process of registering a new Facebook account by:
    - Renewing the Tor connection to ensure anonymity.
    - Generating a random user agent and geolocation.
    - Launching a Firefox browser instance via Playwright with Tor proxy.
    - Handling cookies and "Get started" banners.
    - Filling out the registration form with human-like interactions.
    - Submitting the registration form and handling email confirmation.

    Args:
        temp_email (str): The temporary email address used for registration.
        sid_token (str): The SID token for retrieving the email confirmation code.

    Returns:
        bool: True if registration is successful, False otherwise.

    Raises:
        TimeoutError: If there is a timeout while waiting for the registration form or email confirmation field.
        Exception: If any other error occurs during the registration process.
    """
    logger.info("Starting register_facebook_account")
    try:

        try:
            await page.wait_for_selector('input[name="firstname"]', timeout=120000)
            logger.info("Registration form loaded.")
        except TimeoutError:
            logger.exception("Timeout while waiting for registration form.")
            await page.screenshot(path="logs/form_load_timeout.png")
            await browser.close()
            return

        # Fill out the form with human-like actions
        try:

            await human_typing(page, 'input[name="firstname"]', fake.first_name())
            await human_typing(page, 'input[name="lastname"]', fake.last_name())
            await human_typing(page, 'input[name="reg_email__"]', temp_email)
            await human_typing(page, 'input[name="reg_passwd__"]', fake.password())

            birth_day = str(fake.random_int(min=1, max=28))
            birth_month = str(fake.random_int(min=1, max=12))
            birth_year = str(fake.random_int(min=1960, max=2000))

            await page.select_option('select[name="birthday_day"]', birth_day)
            await page.select_option('select[name="birthday_month"]', birth_month)
            await page.select_option('select[name="birthday_year"]', birth_year)

            logger.info(f"Filled birthday: {birth_day}-{birth_month}-{birth_year}")

            logger.info("Waiting for email confirmation field to appear")
            await page.wait_for_selector(
                'input[name="reg_email_confirmation__"]', timeout=120000
            )
            await human_typing(
                page, 'input[name="reg_email_confirmation__"]', temp_email
            )

            gender = fake.random_element(elements=("1", "2"))
            await page.wait_for_selector(
                f'input[name="sex"][value="{gender}"]', state="visible"
            )
            await page.check(f'input[name="sex"][value="{gender}"]')
            logger.info(f"Gender selected: {gender}")

            logger.info("Submitting the registration form")
            await page.click('button[name="websubmit"]')

            if await handle_confirmation_code(
                page,
                'input[name="code"]',
                'button[name="confirm"]',
                sid_token,
                timeout=30000,
            ):
                # Check if the "Okay" button is present
                try:
                    await page.wait_for_selector(
                        'a[role="button"]:has-text("Okay")', timeout=15000
                    )
                    logger.info("Registration successful. 'Okay' button found.")
                    await browser.close()
                    logger.info(f"Registration successful {temp_email}.")
                    return True
                except TimeoutError:
                    logger.info("Registration not confirmed. 'Okay' button not found.")
                    await browser.close()
                    logger.info("Registration not successful")
                    return False

            try:
                continue_button = await page.wait_for_selector(
                    'div[aria-label="Continue"][role="button"]', timeout=50000
                )
                await continue_button.click()

                logger.info(f"Successfully registered: {temp_email}")
                await browser.close()
                return True

            except TimeoutError:
                logger.exception(f"Failed to register {temp_email}")
                screenshot_path = f"logs/screenshot_{temp_email}.png"
                await page.screenshot(path=screenshot_path)
                await browser.close()
                return False

        except Exception as e:
            logger.exception("Error during registration")
            try:
                if page and not page.is_closed():
                    screenshot_path = f"logs/screenshot_{temp_email}.png"
                    await page.screenshot(path=screenshot_path)
                    logger.exception(f"Screenshot saved to {screenshot_path}")
                    return False
            except Exception as screenshot_error:
                logger.exception("Failed to take screenshot")
            await browser.close()
            return False

    except Exception as e:
        logger.exception("Unexpected error")
        try:
            if page and not page.is_closed():
                screenshot_path = f"logs/screenshot_{temp_email}.png"
                await page.screenshot(path=screenshot_path)
                logger.exception(f"Screenshot saved to {screenshot_path}")
                return False
        except Exception as screenshot_error:
            logger.exception("Failed to take screenshot")
        return False


def get_temp_email():
    """
    Obtain a temporary email address and its corresponding SID token.

    This function sends a request to the Guerrilla Mail API to get a temporary email address.
    It extracts and returns the email address and SID token if successful.

    Returns:
        tuple: A tuple containing the temporary email address (str) and SID token (str),
               or None if the request fails or the email data is not available.

    Raises:
        requests.RequestException: If there is an issue with the HTTP request.
    """
    try:
        response = requests.get(
            "https://api.guerrillamail.com/ajax.php?f=get_email_address"
        )
        response.raise_for_status()

        email_data = response.json()
        if "email_addr" in email_data:
            temp_email = email_data["email_addr"]
            sid_token = email_data["sid_token"]
            logger.info(
                f"Obtained temporary email: {temp_email} sid_token: {sid_token}"
            )
            return temp_email, sid_token
        else:
            logger.exception("Failed to obtain temporary email.")
            return None
    except requests.RequestException as e:
        logger.exception(f"Error obtaining temporary email: {str(e)}")
        return None


def modify_image(image_url):
    """
    Fetch and modify an image from a given URL.

    This function fetches an image from the specified URL, resizes it to 256x256 pixels,
    and returns the modified image. If an error occurs, it logs the error and returns None.

    Args:
        image_url (str): The URL of the image to be fetched and modified.

    Returns:
        numpy.ndarray: The modified image as a numpy array, or None if an error occurs.

    Raises:
        Exception: If there is an error in fetching or modifying the image.
    """
    try:
        logger.info(f"Fetching image from {image_url}")
        image = io.imread(image_url)
        logger.info("Modifying image")
        modified_image = transform.resize(image, (256, 256))
        return modified_image
    except Exception as e:
        logger.exception(f"Error modifying image: {str(e)}")
        return None


async def register_accounts(num_accounts):
    """
    Asynchronously registers multiple Facebook accounts using temporary emails.

    This function fetches temporary emails, creates tasks for each registration
    using the provided `connection` function, and gathers the results.

    Args:
        num_accounts (int): The number of Facebook accounts to register.

    Returns:
        list: A list of dictionaries, where each dictionary contains
              the following keys:
              - "email" (str): The temporary email address used for registration.
                              If email acquisition fails, it will be None.
              - "status" (str): The registration status, either "registered" or "failed".

    Raises:
        Exception: Any exception raised during the asynchronous operations
                   within the function.
    """
    tasks = []
    results = []
    email_list = []

    for _ in range(num_accounts):
        temp_email, sid_token = await sync_to_async(get_temp_email)()
        if temp_email and sid_token:
            browser_type = random.choice(["webkit", "firefox", "chromium"])
            task = asyncio.create_task(connection(temp_email, sid_token, browser_type))
            tasks.append(task)
            email_list.append(temp_email)
        else:
            results.append({"email": None, "status": "failed"})

    # Execute all tasks in parallel
    await asyncio.gather(*tasks)
    logger.info(f"tasks: {tasks}")
    logger.info(f"email_list: {email_list}")

    for index, task in enumerate(tasks):
        try:
            success = await task  # Awaiting the task to get the result
            # Process the task result
            results.append(
                {
                    "email": email_list[index],
                    "status": "registered" if success else "failed",
                }
            )
        except Exception as e:
            logger.exception(
                f"Error processing task for email {email_list[index]}: {e}"
            )
            results.append(
                {
                    "email": email_list[index],
                    "status": "failed",
                }
            )

    return results


class RegisterView(APIView):
    """
    API endpoint for registering multiple Facebook accounts using temporary emails.

    This view handles POST requests to register the specified number of Facebook accounts
    using temporary email addresses. The number of accounts to be registered is provided
    in the request data.

    Authentication and Permissions:
        - Requires basic authentication.
        - Only authenticated users are allowed to access this endpoint.

    Methods:
        post(request): Handles POST requests to register multiple Facebook accounts.

    Args:
        request (HttpRequest): The request object containing the data for registration.

    Returns:
        JsonResponse: A JSON response containing the results of the registration attempts.
            - If the input data is valid, the response is a list of dictionaries, each containing
              the temporary email and the registration status ("registered" or "failed").
            - If the input data is invalid, the response contains an error message with a 400 status code.

    Usage:
        Send a POST request to this endpoint with the following JSON payload:
        {
            "num_accounts": <number_of_accounts_to_register>
        }

    Example Response:
        [
            {"email": "example1@mail.com", "status": "registered"},
            {"email": "example2@mail.com", "status": "failed"},
            ...
        ]
    """

    authentication_classes = [BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logger.info("Received registration request.")
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            num_accounts = serializer.validated_data["num_accounts"]
            try:
                logger.info(f"Starting registration of {num_accounts} accounts.")
                results = async_to_sync(register_accounts)(num_accounts)
                logger.info("Registration process finished.")
                return JsonResponse(results, safe=False)
            except Exception as e:
                logger.exception(f"Error during registration: {e}")
                return JsonResponse({"error": str(e)}, status=500)
        logger.warning("Invalid input data received.")
        return JsonResponse({"error": "Invalid input"}, status=400)
