#!/usr/bin/env python3
import os

import aws_cdk as cdk

from service_proxy.service_proxy_stack import ServiceProxyStack


app = cdk.App()
ServiceProxyStack(app, "ServiceProxyStack",
    env=cdk.Environment(
        account=os.getenv('CDK_DEFAULT_ACCOUNT'),
        region=os.getenv('CDK_DEFAULT_REGION')
    ),
)

app.synth()