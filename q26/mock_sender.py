import hmac
import hashlib
import json
import os
import sys
import time
import httpx

TARGET_URL = os.getenv("WEBHOOK_URL", "http://127.0.0.1:8000/webhooks/events")
SECRET = os.getenv("WEBHOOK_SECRET", "prod_webhook_signing_secret_xyz123")


def sign_payload(body_bytes: bytes, timestamp_str: str, secret_key: str) -> str:
    message = f"{timestamp_str}.".encode("utf-8") + body_bytes
    digest = hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def send_delivery(client: httpx.Client, description: str, body_bytes: bytes, headers: dict):
    print(f"\n--- Scenario: {description} ---")
    start = time.perf_counter()
    response = client.post(TARGET_URL, content=body_bytes, headers=headers)
    elapsed_ms = (time.perf_counter() - start) * 1000

    print(f"Status Code : {response.status_code}")
    print(f"Latency     : {elapsed_ms:.2f} ms")
    print(f"Response    : {response.text}")
    return response


def main():
    print(f"Running mock_sender against: {TARGET_URL}")
    with httpx.Client(timeout=5.0) as client:
        # Case 1: Valid new event
        event_1 = {"id": "evt_prod_001", "type": "payment.succeeded", "created": int(time.time()), "data": {"amount": 2500}}
        raw_1 = json.dumps(event_1).encode("utf-8")
        ts_1 = str(int(time.time()))
        headers_1 = {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": ts_1,
            "X-Webhook-Signature": sign_payload(raw_1, ts_1, SECRET),
        }
        send_delivery(client, "1. Valid, new event (Expect 2xx)", raw_1, headers_1)

        # Case 2: Duplicate delivery of the exact same event
        send_delivery(client, "2. Valid, duplicate event redelivery (Expect 2xx)", raw_1, headers_1)

        # Case 3: Tampered payload body (Signature mismatch)
        tampered_body = json.dumps({"id": "evt_prod_001", "type": "payment.succeeded", "data": {"amount": 999999}}).encode("utf-8")
        headers_tampered = {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": ts_1,
            "X-Webhook-Signature": sign_payload(raw_1, ts_1, SECRET),  # signed against raw_1, not tampered_body
        }
        send_delivery(client, "3. Tampered payload body (Expect 401)", tampered_body, headers_tampered)

        # Case 4: Stale timestamp (Replay attack simulation - 10 minutes ago)
        stale_ts = str(int(time.time()) - 600)
        headers_stale = {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": stale_ts,
            "X-Webhook-Signature": sign_payload(raw_1, stale_ts, SECRET),
        }
        send_delivery(client, "4. Stale timestamp / Replay attack (Expect 401)", raw_1, headers_stale)

        # Case 5: Signed with wrong secret
        headers_wrong_key = {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": ts_1,
            "X-Webhook-Signature": sign_payload(raw_1, ts_1, "incorrect_key"),
        }
        send_delivery(client, "5. Signed with wrong secret (Expect 401)", raw_1, headers_wrong_key)

        # Case 6: Non-JSON body
        raw_non_json = b"INVALID_RAW_TEXT_NOT_JSON"
        ts_non_json = str(int(time.time()))
        headers_non_json = {
            "Content-Type": "text/plain",
            "X-Webhook-Timestamp": ts_non_json,
            "X-Webhook-Signature": sign_payload(raw_non_json, ts_non_json, SECRET),
        }
        send_delivery(client, "6. Body that isn't JSON (Expect 400)", raw_non_json, headers_non_json)

        # Case 7: JSON with missing 'id'
        event_no_id = {"type": "payment.succeeded", "data": {}}
        raw_no_id = json.dumps(event_no_id).encode("utf-8")
        ts_no_id = str(int(time.time()))
        headers_no_id = {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": ts_no_id,
            "X-Webhook-Signature": sign_payload(raw_no_id, ts_no_id, SECRET),
        }
        send_delivery(client, "7. Valid JSON missing 'id' (Expect 400)", raw_no_id, headers_no_id)


if __name__ == "__main__":
    main()