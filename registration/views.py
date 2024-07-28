import asyncio
import logging
import os
import pdb
import time
import random
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

# Настройка логирования
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

# Список рандомных локаций
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
        logger.error(f"Error renewing TOR connection: {str(e)}")


def get_confirmation_code(sid_token):
    try:
        time.sleep(25)  # Ждем некоторое время, чтобы письмо пришло

        response = requests.get(
            f"https://api.guerrillamail.com/ajax.php?f=check_email&seq=0&sid_token={sid_token}"
        )

        response.raise_for_status()  # Проверка, что запрос завершился успешно

        email_data = response.json()
        emails = email_data.get("list")

        if emails:
            confirmation_code = emails[0].get("mail_excerpt")
            if confirmation_code:
                logger.info("Confirmation code found.")
                return confirmation_code
            else:
                logger.info("No confirmation code found in the email excerpt.")
                return None
        else:
            logger.info("No emails found.")
            return None

    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error occurred: {e}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request exception: {e}")
    except requests.exceptions.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        logger.error(f"Response text: {response.text if response else 'No response'}")
    except KeyError as e:
        logger.error(f"Key error: {e}")
    except Exception as e:
        logger.error(f"An error occurred: {e}")

    return None


async def close_cookies_banner(page):
    try:
        # Поиск элемента с ролью "button" и названием "Allow all cookies"
        button = page.get_by_role("button", name="Allow all cookies")

        if await button.is_visible():
            # Получение координат и размеров элемента
            box = await button.bounding_box()
            if box:
                # Имитация перемещения мыши к центру элемента
                await page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await page.wait_for_timeout(500)  # Пауза для имитации задержки

                # Имитация клика по элементу
                await button.click()
                logger.info("Cookies banner found and closed.")
            else:
                logger.info("Bounding box not found for cookies button.")
        else:
            logger.info("No cookies banner found.")
    except TimeoutError as e:
        logger.error(f"Timeout error closing cookies banner: {e}")
    except Exception as e:
        logger.error(f"Error closing cookies banner: {e}")


async def get_started_button(page):
    try:
        # Поиск элемента с ролью "button" и названием "Get started"
        button = page.get_by_role("button", name="Get started")

        if await button.is_visible():
            # Получение координат и размеров элемента
            box = await button.bounding_box()
            if box:
                # Имитация перемещения мыши к центру элемента
                await page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await page.wait_for_timeout(500)  # Пауза для имитации задержки

                # Имитация клика по элементу
                await button.click()
                logger.info("Get started banner found and closed.")
            else:
                logger.info("Bounding box not found for Get started button.")
        else:
            logger.info("Get started banner not found.")
    except TimeoutError as e:
        logger.error(f"Timeout error closing Get started banner: {e}")
    except Exception as e:
        logger.error(f"Error closing Get started banner: {e}")


async def handle_cookies_banner(page):
    try:
        # Поиск элемента с заголовком "Allow all cookies"
        button_cookies = page.get_by_title("Allow all cookies")

        if await button_cookies.is_visible():
            # Получение координат и размеров элемента
            box = await button_cookies.bounding_box()
            if box:
                # Имитация перемещения мыши к центру элемента
                await page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await page.wait_for_timeout(500)  # Пауза для имитации задержки

                # Имитация клика по элементу
                await button_cookies.click()
                logger.info("handle_cookies_banner - Cookies banner found and closed.")
                await get_started_button(page)
            else:
                logger.info(
                    "handle_cookies_banner - Bounding box not found for cookies button."
                )
        else:
            logger.info("handle_cookies_banner - No cookies banner found.")
    except TimeoutError as e:
        logger.error(
            f"handle_cookies_banner - Timeout error closing cookies banner: {e}"
        )
    except Exception as e:
        logger.error(f"handle_cookies_banner - Error closing cookies banner: {e}")


async def register_facebook_account(temp_email, sid_token):
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

            await page.wait_for_timeout(
                2000
            )  # Подождать немного после загрузки страницы

            await handle_cookies_banner(page)
            await close_cookies_banner(page)

            # Проверка наличия элемента
            try:
                await page.wait_for_selector('input[name="firstname"]', timeout=120000)
                logger.info("Registration form loaded.")
            except TimeoutError:
                logger.error("Timeout while waiting for registration form.")
                await page.screenshot(path="logs/form_load_timeout.png")
                await browser.close()
                return

            # Заполнение формы с имитацией человеческих действий
            try:
                # Функция для имитации задержки и движения мыши
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

                        if await page.is_visible("selector-for-success-element"):
                            logger.info(f"Successfully registered {temp_email}")
                            await browser.close()
                            return True
                        else:
                            logger.error(f"Failed to register {temp_email}")
                            await browser.close()
                            return False
                    else:
                        logger.error(
                            f"Failed to retrieve confirmation code for {temp_email}"
                        )
                        await browser.close()
                        return False

                if page.is_visible('div[aria-label="Continue"][role="button"]'):
                    logger.info(f"Successfully registered {temp_email}")
                    return True

                else:
                    logger.error(f"Failed to register {temp_email}")
                    await browser.close()
                    return False

            except Exception as e:
                logger.exception("Error during registration")
                try:
                    if page and not page.is_closed():
                        screenshot_path = f"logs/screenshot_{temp_email}.png"
                        await page.screenshot(path=screenshot_path)
                        logger.error(f"Screenshot saved to {screenshot_path}")
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
                logger.error(f"Screenshot saved to {screenshot_path}")
        except Exception as screenshot_error:
            logger.exception("Failed to take screenshot")
        return False


def get_temp_email():
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
            logger.error("Failed to obtain temporary email.")
            return None
    except requests.RequestException as e:
        logger.error(f"Error obtaining temporary email: {str(e)}")
        return None


def modify_image(image_url):
    try:
        logger.info(f"Fetching image from {image_url}")
        image = io.imread(image_url)
        logger.info("Modifying image")
        modified_image = transform.resize(image, (256, 256))
        return modified_image
    except Exception as e:
        logger.error(f"Error modifying image: {str(e)}")
        return None


class RegisterView(APIView):
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
