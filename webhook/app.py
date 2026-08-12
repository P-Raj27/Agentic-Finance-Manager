import boto3, json

client = boto3.client("bedrock-agentcore", region_name="us-east-1")
try:
    response = client.invoke_agent_runtime(
        agentRuntimeArn="arn:aws:bedrock-agentcore:us-east-1:599942835003:runtime/FinanceManager2_MyAgent-dnWLBy6yct",
        runtimeSessionId="775842592300000000000000000000000",
        payload=json.dumps({"prompt": "Hi"}).encode("utf-8"),
    )
    print(response["response"].read())
except Exception as e:
    print(type(e))
    print(e)
    print(getattr(e, "response", None))