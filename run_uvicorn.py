import subprocess
import os

os.environ["DJANGO_SETTINGS_MODULE"] = "fb_reg.settings"

subprocess.run(
    [
        "uvicorn",
        "fb_reg.asgi:application",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--reload",
    ],
)
