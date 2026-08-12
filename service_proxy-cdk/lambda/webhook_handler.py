import json
import os
import boto3

sqs = boto3.client("sqs")
QUEUE_URL = os.environ["QUEUE_URL"]


def handler(event, context):
    try:
        update = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError as e:
        print(f"Invalid JSON body: {e}")
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    try:
        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")
        user_name = message.get("from", {}).get("username")
        message_id = message.get("message_id", "1")
        transaction_id = f"{chat_id}_{message_id}"
        epoch = message.get("date")

        if not chat_id:
            print("No chat_id in update, ignoring:", json.dumps(update))
            return {"statusCode": 200, "body": json.dumps({"ok": True})}

        print(f"From {chat_id} ({user_name}): {text} -- enqueueing")

        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({
                "chat_id": chat_id,
                "text": text,
                "user_name": user_name,
                "transaction_id": transaction_id,
                "epoch": epoch,
            }),
            MessageGroupId=str(chat_id),          # ensures ordering PER chat — this is the key FIFO piece
            MessageDeduplicationId=transaction_id,  # prevents the same Telegram update being processed twice
        )

    except Exception as e:
        print(f"Unhandled error in webhook handler: {e}")

    return {"statusCode": 200, "body": json.dumps({"ok": True})}