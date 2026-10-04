import boto3
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import urllib.error

ssm = boto3.client("ssm")

BYBIT_URL = "https://api.bybit.com"
RECV_WINDOW = "5000"

# SAFETY SWITCH
# True  = report what would be changed, but DON'T change anything
# False = actually enable AMR
DRY_RUN = True


def get_secret(name):
    return ssm.get_parameter(
        Name=name,
        WithDecryption=True
    )["Parameter"]["Value"].strip()


def make_signature(api_key, api_secret, timestamp, payload):
    string_to_sign = (
        timestamp
        + api_key
        + RECV_WINDOW
        + payload
    )

    return hmac.new(
        api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def bybit_get(api_key, api_secret, path, params):

    query_string = urllib.parse.urlencode(
        sorted(params.items())
    )

    timestamp = str(int(time.time() * 1000))

    signature = make_signature(
        api_key,
        api_secret,
        timestamp,
        query_string
    )

    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW
    }

    url = f"{BYBIT_URL}{path}?{query_string}"

    request = urllib.request.Request(
        url,
        headers=headers,
        method="GET"
    )

    with urllib.request.urlopen(
        request,
        timeout=10
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def bybit_post(api_key, api_secret, path, body):

    # The exact JSON we sign is also the exact JSON sent to Bybit.
    json_body = json.dumps(
        body,
        separators=(",", ":")
    )

    timestamp = str(int(time.time() * 1000))

    signature = make_signature(
        api_key,
        api_secret,
        timestamp,
        json_body
    )

    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW
    }

    request = urllib.request.Request(
        f"{BYBIT_URL}{path}",
        data=json_body.encode("utf-8"),
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(
        request,
        timeout=10
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def lambda_handler(event, context):

    api_key = get_secret(
        "/trading/amr/bybit_api_key"
    )

    api_secret = get_secret(
        "/trading/amr/bybit_api_secret"
    )

    try:

        data = bybit_get(
            api_key,
            api_secret,
            "/v5/position/list",
            {
                "category": "linear",
                "settleCoin": "USDT"
            }
        )

        if data.get("retCode") != 0:
            result = {
                "statusCode": 400,
                "message": "Could not read positions",
                "bybit": data
            }
            print(json.dumps(result))
            return result

        results = []

        for position in data["result"]["list"]:

            size = float(
                position.get("size", "0")
            )

            if size <= 0:
                continue

            symbol = position.get("symbol")
            auto_margin = position.get(
                "autoAddMargin"
            )

            position_idx = position.get(
                "positionIdx",
                0
            )

            # Already enabled
            if auto_margin in [1, "1"]:

                results.append({
                    "symbol": symbol,
                    "result": "AMR already ON"
                })

                continue

            # Safety test mode
            if DRY_RUN:

                results.append({
                    "symbol": symbol,
                    "result": "WOULD ENABLE AMR"
                })

                continue

            # Actually switch AMR on
            response = bybit_post(
                api_key,
                api_secret,
                "/v5/position/set-auto-add-margin",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "autoAddMargin": 1,
                    "positionIdx": position_idx
                }
            )

            if response.get("retCode") == 0:

                results.append({
                    "symbol": symbol,
                    "result": "AMR ENABLED"
                })

            else:

                results.append({
                    "symbol": symbol,
                    "result": "ERROR",
                    "retCode": response.get(
                        "retCode"
                    ),
                    "retMsg": response.get(
                        "retMsg"
                    )
                })

        result = {
            "statusCode": 200,
            "dryRun": DRY_RUN,
            "positionsChecked": len(results),
            "results": results
        }

        print(json.dumps(result))
        return result

    except urllib.error.HTTPError as e:

        result = {
            "statusCode": e.code,
            "error": e.read().decode("utf-8")
        }

        print(json.dumps(result))
        return result

    except Exception as e:

        result = {
            "statusCode": 500,
            "error": str(e)
        }

        print(json.dumps(result))
        return result