import httpx

from assertflow.http import capture_response
from assertflow.secrets import MASK, sanitize_url


def test_response_and_url_secret_values_are_masked() -> None:
    response = httpx.Response(
        200,
        headers={"Authorization": "Bearer secret", "X-Request-Id": "safe"},
        json={"access_token": "secret", "profile": {"password": "secret", "id": 7}},
    )

    captured = capture_response(response)

    assert captured["headers"]["authorization"] == MASK
    assert captured["body"]["access_token"] == MASK
    assert captured["body"]["profile"]["password"] == MASK
    assert captured["body"]["profile"]["id"] == 7
    assert sanitize_url("https://api.test/users?api_key=secret&page=2") == (
        "https://api.test/users?api_key=%2A%2A%2A%2A%2A%2A%2A%2A&page=2"
    )
