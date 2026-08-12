from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_event_sources,
    aws_apigateway as apigateway,
    aws_dynamodb as dynamodb,
    aws_ssm as ssm,
    aws_iam as iam,
    aws_sqs as sqs,
    CfnOutput,
)
from constructs import Construct

class ServiceProxyStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = dynamodb.Table(
            self, "ServiceProxyTable",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Pull the AgentCore runtime ARN written by the agentcore deploy stack
        agentcore_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:599942835003:runtime/FinanceManager2_MyAgent-dnWLBy6yct"
        telegram_bot_token = ssm.StringParameter.value_for_string_parameter(
            self, "/telegram/bot_token"
        )

        queue = sqs.Queue(
            self, "TelegramMessageQueue",
            queue_name="telegram-messages.fifo",
            fifo=True,
            content_based_deduplication=False,  # explicit MessageDeduplicationId used instead
            visibility_timeout=Duration.seconds(320),  # should exceed worker Lambda timeout
            removal_policy=RemovalPolicy.DESTROY,
        )

        worker_fn = _lambda.Function(
            self, "AgentWorkerHandler",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="worker_handler.handler",
            code=_lambda.Code.from_asset("lambda"),
            timeout=Duration.seconds(300),
            environment={
                "TABLE_NAME": table.table_name,
                "AGENTCORE_RUNTIME_ARN": agentcore_runtime_arn,
                "TELEGRAM_BOT_TOKEN": telegram_bot_token,
            },
        )
        worker_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=["*"],
            )
        )
        worker_fn.add_event_source(
            lambda_event_sources.SqsEventSource(
                queue,
                batch_size=1,
                report_batch_item_failures=True,
            )
        )

        webhook_fn = _lambda.Function(
            self, "TelegramWebhookHandler",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="webhook_handler.handler",
            code=_lambda.Code.from_asset("lambda"),
            timeout=Duration.seconds(15),
            environment={
                "TABLE_NAME": table.table_name,
                "AGENTCORE_RUNTIME_ARN": agentcore_runtime_arn,
                "TELEGRAM_BOT_TOKEN": telegram_bot_token,
                "QUEUE_URL": queue.queue_url

            },
        )

        table.grant_read_write_data(webhook_fn)

        # Allow the Lambda to invoke the AgentCore runtime
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[agentcore_runtime_arn],
            )
        )

        api = apigateway.RestApi(
            self, "ServiceProxyApi",
            rest_api_name="ServiceProxyApi",
        )

        webhook_resource = api.root.add_resource("webhook")
        webhook_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(webhook_fn),
        )
        queue.grant_send_messages(webhook_fn)

        CfnOutput(self, "WebhookUrl", value=f"{api.url}webhook")
        CfnOutput(self, "TableName", value=table.table_name)