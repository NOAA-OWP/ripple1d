"""Run API tests."""

import json
from http import HTTPStatus

import requests
from dotenv import dotenv_values

FLASK_ENV_VARS = dotenv_values(".flaskenv")


def ping():
    """
    Send a GET request to the /ping endpoint of a local server.

    This function constructs the URL for the /ping endpoint using the FLASK_RUN_PORT
    specified in the .flaskenv file. It then sends a GET request to this URL.

    If the response status code is not HTTPStatus.OK (200), it raises an HTTPError with
    the response content and status code. Otherwise, it prints the JSON-decoded response content.
    """
    url = f"http://localhost:{FLASK_ENV_VARS['FLASK_RUN_PORT']}/ping"
    print(f"Pinging at url {repr(url)}")
    r = requests.get(url=url)
    if r.status_code != HTTPStatus.OK:
        raise requests.HTTPError(r.content.decode(), r.status_code)
    response_data = json.loads(r.content)
    print(response_data)


def execute_test_process():
    """
    Send a POST request to initiate a test process on a local server.

    This function constructs the URL for initiating a test process using the FLASK_RUN_PORT
    specified in the .flaskenv file. It then sends a POST request with an empty JSON payload
    to this URL.

    If the response status code is not HTTPStatus.OK (200), it raises an HTTPError with
    the response content and status code. Otherwise, it prints the JSON-decoded response content.
    """
    url = f"http://localhost:{FLASK_ENV_VARS['FLASK_RUN_PORT']}/processes/test/execution"
    print(f"Executing test process at url {repr(url)}")
    r = requests.post(url=url, json={})
    if r.status_code != HTTPStatus.OK:
        raise requests.HTTPError(r.content.decode(), r.status_code)
    response_data = json.loads(r.content)
    print(response_data)


def main():
    """
    Execute the ping and test process functions.

    This function first calls the ping() function to send a GET request to the /ping endpoint.
    Then, it calls the execute_test_process() function to initiate a test process by sending
    a POST request to the relevant endpoint.
    """
    ping()
    execute_test_process()


if __name__ == "__main__":
    main()
