# Facebook Account Registration Automation

This project automates the process of registering multiple Facebook accounts using temporary email addresses. It utilizes the Playwright library for browser automation, Guerrilla Mail for temporary email addresses, and Django Rest Framework (DRF) for creating API endpoints.

## Features

- Automatically fetches temporary email addresses.
- Fills out the Facebook registration form with randomly generated user data.
- Submits the registration form and handles email confirmation.
- Logs the results of the registration attempts.

## Requirements

- Python 3.8+
- Django
- Django Rest Framework
- Playwright
- Guerrilla Mail API
- Requests
- Faker
- TOR (for proxy)

## Installation

1. Clone the repository:

    ```bash
    git clone https://github.com/igor20192/FBAccountMaker.git
    cd FBAccountMaker
    ```

2. Create and activate a virtual environment:

    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3. Install the required packages:

    ```bash
    pip install -r requirements.txt
    ```

4. Install Playwright and its dependencies:

    ```bash
    playwright install
    ```

5. Ensure TOR is installed and running on your machine.

## Configuration

Configure the following settings in your Django project settings:

- `ALLOWED_HOSTS`
- `DATABASES`
- `INSTALLED_APPS` (Add `rest_framework` `registration`)

## Usage

### Running the Django Server

1. Apply migrations and create a superuser:

    ```bash
    python manage.py migrate
    python manage.py createsuperuser
    ```

2. Start the Django development server:

    ```bash
    python manage.py runserver
    ```

### Making API Requests

Use a tool like `curl` or Postman to make POST requests to the registration endpoint. The endpoint expects a JSON payload with the number of accounts to be registered.

Example request:

```json
POST /api/register/
{
    "num_accounts": 5
}
```

### Example response:

```json
[
    {"email": "example1@mail.com", "status": "registered"},
    {"email": "example2@mail.com", "status": "failed"},
    ...
]
```

## Project Structure

- views.py: Contains the API endpoint for registering Facebook accounts.
- serializers.py: Defines the serializer for the input data.
- utils.py: Contains utility functions for handling cookies and modifying images.
- logs/: Directory where screenshots and logs are saved.

## Code Documentation

`register_facebook_account`
Registers a Facebook account using a temporary email address.

`get_temp_email`
Fetches a temporary email address from the Guerrilla Mail API.

`modify_image`
Modifies an image by resizing it to 256x256 pixels.

`handle_cookies_banner`
Handles closing the cookies banner on the Facebook registration page.

`close_cookies_banner`
Closes the cookies banner on the Facebook registration page.

`get_started_button`
Handles clicking the "Get Started" button on the Facebook registration page.

`RegisterView`
API endpoint for registering multiple Facebook accounts using temporary emails.

## Logging
Logs are saved in the logs/ directory. Each registration attempt's result is logged, along with any errors encountered during the process.

## License
This project is licensed under the MIT License. See the LICENSE file for more details.

## Contributing
Contributions are welcome! Please open an issue or submit a pull request with your changes.

## Contact
For any inquiries, please contact [igor.udovenko2015@gmail.com].

This README provides an overview of the project, including its features, installation instructions, usage guidelines, project structure, code documentation, and other relevant information. You can customize it further based on your specific needs and preferences.

