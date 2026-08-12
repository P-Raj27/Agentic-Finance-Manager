import json
import os
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

import boto3

agentcore_client = boto3.client("bedrock-agentcore")

AGENTCORE_RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


def epoch_to_ist_iso(epoch):
    if not epoch:
        return ""
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.fromtimestamp(epoch, tz=ist).isoformat()


def make_session_id(chat_id):
    base = f"telegram-chat-{chat_id}"
    return base.ljust(33, "0")


def call_agent(message, chat_id, user_name, transaction_id, iso):
    prompt = f"""User Message: {message}
User Id: {chat_id}
UserName: {user_name}
TransactionId: {transaction_id}
If Date and time is not provided only then use this ISO string to get date and time {iso}"""

    try:
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
            runtimeSessionId=make_session_id(chat_id),
            payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        )
        raw_body = response["response"].read().decode("utf-8")
        if not raw_body.strip():
            return "E.V is unable to respond, please try again after sometime"
        parsed = json.loads(raw_body)
        return parsed["result"]["content"][0]["text"]
    except Exception as e:
        print(f"Error communicating with AgentCore: {e}")
        raise  # re-raise so SQS treats this as a failed batch item and retries per redrive policy


def send_message(chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                print("Telegram API error:", result)
            return result
    except urllib.error.HTTPError as e:
        print(f"Telegram HTTP Error {e.code}: {e.reason}")
        print("Details:", e.read().decode("utf-8"))
    except Exception as e:
        print(f"Failed to send message: {e}")


def handler(event, context):
    batch_item_failures = []

    for record in event["Records"]:
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            chat_id = body["chat_id"]
            text = body["text"]
            user_name = body["user_name"]
            transaction_id = body["transaction_id"]
            iso = epoch_to_ist_iso(body.get("epoch"))

            print(f"[worker] From {chat_id} ({user_name}): {text}")
            agent_response = call_agent(text, chat_id, user_name, transaction_id, iso)
            send_message(chat_id, agent_response)

        except Exception as e:
            print(f"Failed processing message {message_id}: {e}")
            batch_item_failures.append({"itemIdentifier": message_id})

    # Only messages listed here get retried / redriven to DLQ — successful ones are removed from the queue
    return {"batchItemFailures": batch_item_failures}