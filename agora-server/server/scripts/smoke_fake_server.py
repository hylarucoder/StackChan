import json
import os
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler

SmokeCheck = namedtuple("SmokeCheck", ["name", "method", "path", "body", "expected_action"])
LOCAL_OPENER = build_opener(ProxyHandler({}))


def build_smoke_checks(base_url):
    del base_url
    return [
        SmokeCheck("get_config", "GET", "/get_config?channel=stackchan-poc&uid=10001", None, None),
        SmokeCheck(
            "start_agent",
            "POST",
            "/v2/startAgent",
            {
                "channelName": "stackchan-poc",
                "rtcUid": 20001,
                "userUid": 10001,
                "parameters": {"output_audio_codec": "g722"},
            },
            None,
        ),
        SmokeCheck(
            "resolve_dance_command",
            "POST",
            "/device/commands/resolve",
            {"deviceId": "stackchan-1", "text": "come dance"},
            "dance",
        ),
        SmokeCheck("poll_dance_command", "GET", "/device/commands/next?deviceId=stackchan-1", None, "dance"),
        SmokeCheck(
            "queue_stop_command",
            "POST",
            "/device/commands",
            {"deviceId": "stackchan-1", "action": "stop", "source": "smoke"},
            "stop",
        ),
        SmokeCheck("poll_stop_command", "GET", "/device/commands/next?deviceId=stackchan-1", None, "stop"),
    ]


def request_json(base_url, check, timeout=5):
    body = None
    headers = {}
    if check.body is not None:
        body = json.dumps(check.body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(base_url + check.path, data=body, headers=headers, method=check.method)
    with LOCAL_OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def assert_check_response(check, payload):
    if payload.get("code") != 0:
        raise AssertionError(f"{check.name} returned non-zero code: {payload}")

    if check.expected_action is None:
        return

    command = payload.get("data", {}).get("command", {})
    action = command.get("action")
    if action != check.expected_action:
        raise AssertionError(f"{check.name} expected action={check.expected_action}, got {action}: {payload}")


def wait_for_server(base_url, timeout_seconds=10):
    deadline = time.time() + timeout_seconds
    check = SmokeCheck("wait_get_config", "GET", "/get_config?channel=wait&uid=10001", None, None)
    last_error = None
    while time.time() < deadline:
        try:
            request_json(base_url, check, timeout=1)
            return
        except (OSError, URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"fake server did not become ready: {last_error}")


def run_smoke(base_url):
    for check in build_smoke_checks(base_url):
        payload = request_json(base_url, check)
        assert_check_response(check, payload)
        print(f"ok {check.name}")


def main():
    server_root = Path(__file__).resolve().parents[1]
    port = int(os.getenv("PORT", "8765"))
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PORT"] = str(port)

    process = subprocess.Popen(
        [sys.executable, str(server_root / "scripts" / "run_fake_server.py")],
        cwd=str(server_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_server(base_url)
        run_smoke(base_url)
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
        if process.returncode not in (0, -15):
            print(output)
            raise SystemExit(process.returncode)


if __name__ == "__main__":
    main()
