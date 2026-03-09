import json
import os

import boto3

_api_keys = None


def _get_api_keys():
    global _api_keys
    if _api_keys is not None:
        return _api_keys

    ssm = boto3.client("ssm")
    primary = ssm.get_parameter(
        Name="/erith-launch/admiralty-api-key-primary",
        WithDecryption=True,
    )["Parameter"]["Value"]
    secondary = ssm.get_parameter(
        Name="/erith-launch/admiralty-api-key-secondary",
        WithDecryption=True,
    )["Parameter"]["Value"]
    _api_keys = [primary, secondary]
    return _api_keys


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    try:
        trailer_height = float(params.get("trailer_height", 0.5))
        yacht_draft = float(params.get("yacht_draft", 1.14))
    except (ValueError, TypeError):
        return {
            "statusCode": 400,
            "headers": _headers(),
            "body": json.dumps({"error": "Invalid trailer_height or yacht_draft parameter"}),
        }

    try:
        from tide_logic import get_windows
        api_keys = _get_api_keys()
        windows = get_windows(api_keys, trailer_height=trailer_height, yacht_draft=yacht_draft)
    except RuntimeError as e:
        return {
            "statusCode": 502,
            "headers": _headers(),
            "body": json.dumps({"error": str(e)}),
        }

    return {
        "statusCode": 200,
        "headers": _headers(),
        "body": json.dumps(windows),
    }


def _headers():
    return {
        "Content-Type": "application/json",
    }
