#!/usr/bin/env python3
import os

import aws_cdk as cdk
# import cdk_nag
from cfn.deploy_stack import PAMVideoAnalysis

app = cdk.App()
stack = PAMVideoAnalysis(app, "PAMVideoAnalysis",
    # If you don't specify 'env', this stack will be environment-agnostic.
    # Account/Region-dependent features and context lookups will not work,
    # but a single synthesized template can be deployed anywhere.

    # Uncomment the next line to specialize this stack for the AWS Account
    # and Region that are implied by the current CLI configuration.

    # env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION')),

    # Uncomment the next line if you know exactly what Account and Region you
    # want to deploy the stack to. */

    #env=cdk.Environment(account='123456789012', region='us-east-1'),

    # For more information, see https://docs.aws.amazon.com/cdk/latest/guide/environments.html
    )

# validate the stack against AWS best practices, exceptions being justified below
# cdk.Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(verbose=True))
# cdk_nag.NagSuppressions.add_stack_suppressions(
#     stack,
#     suppressions=[
#         {
#             "id": "AwsSolutions-IAM4",
#             "reason": "This stack's IAM permissions assigned to Lambda functions and Step Function to access other stack resources, including customer data to be processed, are delimited to give them minimum privileges",
#         },
#         {
#             "id": "AwsSolutions-IAM5",
#             "reason": "This stack's IAM permissions assigned to Lambda functions and Step Function to access other stack resources, including customer data to be processed, are delimited to give them minimum privileges",
#         },
#         {
#             "id": "AwsSolutions-L1",
#             "reason": "This stack explicitly uses a minimal Python SDK release as Lambda runtime to make sure the required Bedrock APIs are available for Lambda functions",
#         },
#         {
#             "id": "AwsSolutions-SF1",
#             "reason": "This stack has no auditing SLA and therefore does not need require all events to be logged for Step Functions",
#         },
#         {
#             "id": "AwsSolutions-SF2",
#             "reason": "This stack has no performance SLA and therefore does not need require X-Ray tracing for Step Functions",
#         },
#     ],
# )

app.synth()
