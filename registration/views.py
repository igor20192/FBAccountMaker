import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
import pdb
import time
from django.http import JsonResponse
import playwright
from rest_framework.views import APIView
from .serializers import RegisterSerializer
from playwright.async_api import async_playwright
from fake_useragent import UserAgent
from faker import Faker
import requests
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


def renew_tor_connection():
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate(
                password=config("TOR_PASSWORD")
            )  # Замените your_password на ваш фактический пароль
            controller.signal(Signal.NEWNYM)
            logger.info("TOR connection renewed successfully")
    except Exception as e:
        logger.error(f"Error renewing TOR connection: {str(e)}")


def get_confirmation_code(email_address):
    # Эта функция должна быть изменена в соответствии с вашим способом получения почты
    time.sleep(10)  # Ждем некоторое время, чтобы письмо пришло
    response = requests.get(
        f"https://www.guerrillamail.com/ajax.php?f=get_email_list&email={email_address}"
    )
    email_data = response.json()
    for email in email_data["list"]:
        if "FB-" in email["mail_subject"]:
            email_id = email["mail_id"]
            email_response = requests.get(
                f"https://www.guerrillamail.com/ajax.php?f=fetch_email&email_id={email_id}"
            )
            email_content = email_response.json()["mail_body"]
            confirmation_code = email_content.split("FB-")[1][:5]
            return confirmation_code
    return None


async def register_facebook_account(temp_email):
    try:
        renew_tor_connection()
        user_agent = UserAgent().random
        async with async_playwright() as p:
            browser = await p.firefox.launch(
                proxy={"server": "socks5://127.0.0.1:9050"}, headless=True
            )
            context = await browser.new_context(user_agent=user_agent)
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

            # Проверка наличия элемента
            try:
                await page.wait_for_selector(
                    'input[name="firstname"]', timeout=120000
                )  # Увеличиваем таймаут до 120 секунд
            except Exception as e:
                logger.exception("Failed to find registration form elements")
                await browser.close()
                return

            logger.info(f"Filling the registration form for {temp_email}")

            try:
                await page.fill(
                    'input[name="firstname"]', fake.first_name(), timeout=120000
                )  # Увеличиваем таймаут до 120 секунд
                await page.fill('input[name="lastname"]', fake.last_name())
                await page.fill('input[name="reg_email__"]', temp_email)
                await page.fill('input[name="reg_passwd__"]', fake.password())

                # Заполнение полей дня, месяца и года рождения
                birth_day = str(fake.random_int(min=1, max=28))
                birth_month = str(fake.random_int(min=1, max=12))
                birth_year = str(fake.random_int(min=1960, max=2000))

                await page.select_option('select[name="birthday_day"]', birth_day)
                await page.select_option('select[name="birthday_month"]', birth_month)
                await page.select_option('select[name="birthday_year"]', birth_year)

                logger.info(f"Filled birthday: {birth_day}-{birth_month}-{birth_year}")

                # Заполнение поля подтверждения email
                logger.info("Waiting for email confirmation field to appear")
                await page.wait_for_selector(
                    'input[name="reg_email_confirmation__"]', timeout=120000
                )  # Увеличиваем таймаут до 120 секунд
                await page.fill('input[name="reg_email_confirmation__"]', temp_email)

                # Выбор пола
                gender = fake.random_element(
                    elements=("1", "2")
                )  # 1: Женщина, 2: Мужчина
                await page.wait_for_selector(
                    f'input[name="sex"][value="{gender}"]', state="visible"
                )
                await page.check(f'input[name="sex"][value="{gender}"]')
                logger.info(f"Gender selected: {gender}")

                logger.info(f"Submitting the registration form for {temp_email}")
                await page.click('button[name="websubmit"]')

                # Ждем некоторое время для обработки запроса
                await page.wait_for_timeout(60000)  # ждём 60 секунд

                # Проверяем, успешна ли регистрация и переходит ли на страницу подтверждения
                if await page.is_visible('input[name="code"]'):
                    logger.info(f"Waiting for confirmation code for {temp_email}")
                    confirmation_code = get_confirmation_code(temp_email)

                    if confirmation_code:
                        logger.info(f"Confirmation code received: {confirmation_code}")
                        await page.fill('input[name="code"]', confirmation_code)
                        await page.click('button[name="confirm"]')

                        await page.wait_for_timeout(10000)  # ждём 10 секунд

                        if await page.is_visible("selector-for-success-element"):
                            logger.info(f"Successfully registered {temp_email}")
                            await browser.close()
                            return True
                        else:
                            logger.exception(f"Failed to register {temp_email}")
                            await browser.close()
                            return False
                    else:
                        logger.exception(
                            f"Failed to retrieve confirmation code for {temp_email}"
                        )
                        await browser.close()
                        return False
                else:
                    logger.exception(f"Failed to register {temp_email}")
                    await browser.close()
                    return False

            except Exception as e:
                logger.exception("Error during registration")
                try:
                    # Делать скриншот в случае ошибки, если страница не была закрыта
                    if page and not page.is_closed():
                        screenshot_path = f"logs/screenshot_{temp_email}.png"
                        await page.screenshot(path=screenshot_path)
                        logger.error(f"Screenshot saved to {screenshot_path}")
                except Exception as screenshot_error:
                    logger.exception("Failed to take screenshot")
                    await browser.close()
                return False

    except Exception as e:
        try:
            # Делать скриншот в случае ошибки, если страница не была закрыта
            if page and not page.is_closed():
                screenshot_path = f"logs/screenshot_{temp_email}.png"
                await page.screenshot(path=screenshot_path)
                logger.error(f"Screenshot saved to {screenshot_path}")
        except Exception as screenshot_error:
            logger.exception("Failed to take screenshot")
        return False


def get_temp_email():
    try:
        # Запрос создания нового временного адреса электронной почты
        response = requests.get(
            "https://api.guerrillamail.com/ajax.php?f=get_email_address"
        )
        response.raise_for_status()

        # Получение адреса электронной почты из ответа
        email_data = response.json()
        if "email_addr" in email_data:
            temp_email = email_data["email_addr"]
            logger.info(f"Obtained temporary email: {temp_email}")
            return temp_email
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
                temp_email = get_temp_email()
                if temp_email:
                    asyncio.run(register_facebook_account(temp_email))
                    results.append({"email": temp_email, "status": "registered"})
                else:
                    results.append({"email": None, "status": "failed"})

            return JsonResponse(results, safe=False)
        return JsonResponse({"error": "Invalid input"}, status=400)
