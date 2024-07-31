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
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate(password=config("TOR_PASSWORD"))
            controller.signal(Signal.NEWNYM)
            logger.info("TOR connection renewed successfully")
    except Exception as e:
        logger.exception(f"Error renewing TOR connection: {str(e)}")


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


async def close_cookies_banner(page):
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

        async def human_typing(selector: str, text: str):
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

        await human_typing('input[aria-label="First name"]', fake.first_name())
        await human_typing('input[aria-label="Last name"]', fake.last_name())

        next_button = page.get_by_role("button", name="Next")
        if await click_button(page, next_button, "Next"):
            await page.wait_for_timeout(60000)  # Wait for the next page to load

            # Fill in the date of birth
            birth_date = fake.date_of_birth(minimum_age=18, maximum_age=100)
            birth_date_str = birth_date.strftime("%Y-%m-%d")
            await page.fill('input[type="date"]', birth_date_str)
            logger.info(f"Filled date of birth: {birth_date_str}")

            if await click_button(page, next_button, "Next"):
                await page.wait_for_timeout(30000)  # Wait for the next page to load

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
                            await email_input.fill(temp_email)
                            logger.info(f"Filled email: {temp_email}")
                            if await click_button(page, next_button, "Next"):
                                # Fill in the password
                                await page.wait_for_selector(
                                    'input[aria-label="Password"]', timeout=20000
                                )
                                logger.info("Password input found")
                                await human_typing(
                                    'input[aria-label="Password"]', fake.password()
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
                                            logger.info(
                                                f"Registration successful for {temp_email}."
                                            )
                                            await browser.close()
                                            return True

    except Exception as e:
        logger.exception("Error during registration")
        await handle_error(page, browser, temp_email)

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
    try:
        # Search for the element with role "button" and name "Get started"
        button = page.get_by_role("button", name="Get started")

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
                logger.info("Get started banner found and closed.")
                await register_facebook_account_v2(page, browser, temp_email, sid_token)
            else:
                logger.info("Bounding box not found for Get started button.")
        else:
            logger.info("Get started banner not found.")
    except TimeoutError as e:
        logger.exception(f"Timeout error closing Get started banner: {e}")
    except Exception as e:
        logger.exception(f"Error closing Get started banner: {e}")


async def handle_cookies_banner(page, browser, temp_email, sid_token):
    """
    Close the cookies banner on the page if it is visible.

    This function attempts to locate and click the "Allow all cookies" button on a web page.
    It simulates human-like mouse movements and adds a small delay before clicking the button.
    After closing the cookies banner, it attempts to close the "Get started" banner if present.

    Args:
        page (playwright.async_api.Page): The Playwright page object.

    Returns:
        None

    Raises:
        TimeoutError: If there is a timeout while trying to close the cookies banner.
        Exception: If any other error occurs during the process.
    """
    try:
        # Search for the element with the title "Allow all cookies"
        button_cookies = page.get_by_title("Allow all cookies")

        if await button_cookies.is_visible():
            # Get the coordinates and dimensions of the element
            box = await button_cookies.bounding_box()
            if box:
                # Simulate moving the mouse to the center of the element
                await page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await page.wait_for_timeout(500)  # Pause to simulate delay

                # Simulate clicking the element
                await button_cookies.click()
                logger.info("handle_cookies_banner - Cookies banner found and closed.")
                await get_started_button(page, browser, temp_email, sid_token)
            else:
                logger.info(
                    "handle_cookies_banner - Bounding box not found for cookies button."
                )
        else:
            logger.info("handle_cookies_banner - No cookies banner found.")
    except TimeoutError as e:
        logger.exception(
            f"handle_cookies_banner - Timeout error closing cookies banner: {e}"
        )
    except Exception as e:
        logger.exception(f"handle_cookies_banner - Error closing cookies banner: {e}")


async def register_facebook_account(temp_email, sid_token):
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
    try:
        renew_tor_connection()
        user_agent = UserAgent().random
        location = random.choice(locations)

        async with async_playwright() as p:
            browser = await p.firefox.launch(
                proxy={"server": "socks5://127.0.0.1:9050"}, headless=False
            )
            context = await browser.new_context(
                user_agent=user_agent, geolocation=location, permissions=["geolocation"]
            )
            page = await context.new_page()

            logger.info(f"Opening Facebook registration page for {temp_email}")
            try:
                await page.goto(
                    "https://www.facebook.com/r.php", wait_until="networkidle"
                )
                logger.info("Facebook registration page opened successfully")
            except Exception as e:
                logger.exception("Failed to open Facebook registration page")
                await browser.close()
                return

            await page.wait_for_timeout(5000)  # Wait a bit after the page loads

            await handle_cookies_banner(page, browser, temp_email, sid_token)
            await close_cookies_banner(page)

            # Check for the presence of the registration form
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
                # Function to simulate typing delay and mouse movement
                async def human_typing(page, selector, text):
                    logger.info(text)
                    element = await page.query_selector(selector)
                    if element:
                        box = await element.bounding_box()
                        if box:
                            await page.mouse.move(
                                box["x"] + box["width"] / 2,
                                box["y"] + box["height"] / 2,
                            )
                            await page.wait_for_timeout(random.randint(500, 1000))
                        await page.fill(selector, text)
                        await page.wait_for_timeout(random.randint(500, 2000))

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

                await page.wait_for_timeout(60000)

                if await page.is_visible('input[name="code"]'):
                    logger.info("Waiting for confirmation code")
                    confirmation_code = get_confirmation_code(sid_token)

                    if confirmation_code:
                        logger.info(f"Confirmation code received: {confirmation_code}")
                        await human_typing(
                            page, 'input[name="code"]', confirmation_code
                        )
                        await page.click('button[name="confirm"]')

                        await page.wait_for_timeout(10000)

                        # Check if the "Okay" button is present
                        try:
                            await page.wait_for_selector(
                                'a[role="button"]:has-text("Okay")', timeout=10000
                            )
                            logger.info("Registration successful. 'Okay' button found.")
                            await browser.close()
                            logger.info(f"Registration successful {temp_email}.")
                            return True
                        except TimeoutError:
                            logger.info(
                                "Registration not confirmed. 'Okay' button not found."
                            )
                            await browser.close()
                            logger.info("Registration not successful")
                            return False

                    else:
                        logger.exception(
                            f"Failed to retrieve confirmation code for {temp_email}"
                        )
                        await browser.close()
                        return False

                if await page.is_visible('div[aria-label="Continue"][role="button"]'):
                    logger.info(f"Successfully registered {temp_email}")
                    await browser.close()
                    return True

                else:
                    logger.exception(f"Failed to register {temp_email}")
                    await browser.close()
                    return False

            except Exception as e:
                logger.exception("Error during registration")
                try:
                    if page and not page.is_closed():
                        screenshot_path = f"logs/screenshot_{temp_email}.png"
                        await page.screenshot(path=screenshot_path)
                        logger.exception(f"Screenshot saved to {screenshot_path}")
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
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            num_accounts = serializer.validated_data["num_accounts"]
            results = []

            for _ in range(num_accounts):
                temp_email, sid_token = get_temp_email()
                if temp_email and sid_token:
                    success = asyncio.run(
                        register_facebook_account(temp_email, sid_token)
                    )
                    if success:
                        results.append({"email": temp_email, "status": "registered"})
                    else:
                        results.append({"email": temp_email, "status": "failed"})
                else:
                    results.append({"email": None, "status": "failed"})

            return JsonResponse(results, safe=False)
        return JsonResponse({"error": "Invalid input"}, status=400)
