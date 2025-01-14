from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_bedrock as bedrock,
    aws_events as events,
    aws_events_targets as targets,
    CfnOutput,
)

from constructs import Construct

import os, subprocess, platform

PYTHON_VERSION = lambda_.Runtime.PYTHON_3_12
LAMBDA_TIMEOUT = Duration.seconds(900)

class PAMVideoAnalysis(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        ######################################################
        # S3 buckets to store Video and Image objects  
        ######################################################
        video_bucket_name = self.node.try_get_context("videobucketname")
        if not video_bucket_name:
            video_bucket_name = "Video-Recordings"
        
        video_bucket = s3.Bucket(self, 
                                video_bucket_name, 
                                block_public_access=s3.BlockPublicAccess.BLOCK_ALL, 
                                server_access_logs_prefix="access-logs/",
                                encryption=s3.BucketEncryption.S3_MANAGED, 
                                enforce_ssl=True,
                                event_bridge_enabled=True,
                                auto_delete_objects=True,
                                removal_policy=RemovalPolicy.DESTROY)

        image_bucket_name = self.node.try_get_context("imagebucketname")
        if not image_bucket_name:
            image_bucket_name = "Still-Frame-Images"
        image_bucket = s3.Bucket(self, 
                                image_bucket_name, 
                                block_public_access=s3.BlockPublicAccess.BLOCK_ALL, 
                                server_access_logs_prefix="access-logs/",
                                encryption=s3.BucketEncryption.S3_MANAGED, 
                                enforce_ssl=True,
                                auto_delete_objects=True,
                                removal_policy=RemovalPolicy.DESTROY)        
        
        ######################################################
        # Define the Dynamo DB tables to store Video Transcripts 
        ######################################################
        table_name = self.node.try_get_context("videotablename")
        if not table_name:
            table_name = "VideoTranscriptsTable"
        video_transcripts_table = ddb.TableV2(self, table_name,
            partition_key=ddb.Attribute(name="VideoID", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SequenceID", type=ddb.AttributeType.STRING),
            billing= ddb.Billing.on_demand(),
            table_class= ddb.TableClass.STANDARD,
            encryption=ddb.TableEncryptionV2.dynamo_owned_key(),
            removal_policy=RemovalPolicy.DESTROY
        )
        
        ######################################################
        # Define the Dynamo DB table where LLM prompts 
        # will be stored and pre-fill with default prompts
        # first, create the deployment of the local json 
        # files into s3 for the table import
        ######################################################
        prompt_files_deployment = s3_deploy.BucketDeployment(
            self, 'PromptDeploy', 
            sources=[s3_deploy.Source.asset("prompts/transcribe/"), s3_deploy.Source.asset("prompts/security_analysis/")],
            destination_bucket=image_bucket,
            destination_key_prefix="prompt-config/",                                                
        )
        prompt_files_deployment.node.add_dependency(image_bucket)
        
        # then the table itself
        table_name = self.node.try_get_context("prompttablename")
        if not table_name:
            table_name = "LLMPromptTable"
        prompt_table = ddb.Table(self, table_name,
            partition_key=ddb.Attribute(name="PromptID", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="VersionID", type=ddb.AttributeType.STRING),
            billing_mode= ddb.BillingMode.PAY_PER_REQUEST,
            table_class= ddb.TableClass.STANDARD,
            encryption=ddb.TableEncryption.AWS_MANAGED,
            import_source=ddb.ImportSourceSpecification(
                compression_type=ddb.InputCompressionType.NONE,
                input_format=ddb.InputFormat.dynamo_db_json(),
                bucket=image_bucket,
                key_prefix="prompt-config/"
            ),
            removal_policy=RemovalPolicy.DESTROY
        )
        prompt_table.node.add_dependency(prompt_files_deployment)

        ######################################################
        # Lambda layers 
        ######################################################
        # layer with the latest boto3 and botocore libs
        boto3_lambda_layer = self.__create_layer_from_pip("boto3", "Layer with recent enough Boto3 Library", "1-35-or-higher")
        # layer with the ffmpeg executable
        ffmpeg_layer = self.__create_layer_from_shell("ffmpeg", "Layer with FFmpeg", "7-0-2-or-higher")
        # layer with Lambda powertools
        powertools_layer = lambda_.LayerVersion.from_layer_version_arn(
            self, "PowertoolsLayer",
            layer_version_arn=f"arn:aws:lambda:{Stack.of(self).region}:017000801446:layer:AWSLambdaPowertoolsPythonV2:46"
        )         
        
        ######################################################
        # Define the Lambda functions to create still frame 
        # images from videos, transcribe images and aggregate 
        # transcriptions.
        ######################################################
        create_still_frame_images_function = lambda_.Function(
            self, "Create-Still-Frame-Images-Function",
            code=lambda_.Code.from_asset("lambdas/create_still_frame_images"),
            handler="create_still_frame_images.lambda_handler",
            runtime=PYTHON_VERSION,
            timeout=LAMBDA_TIMEOUT,
            memory_size=512,
            environment={
                "VIDEO_BUCKET": video_bucket.bucket_name,
                "IMAGE_BUCKET": image_bucket.bucket_name,
                "POWERTOOLS_SERVICE_NAME": "pam-video-analysis",
                "POWERTOOLS_METRICS_NAMESPACE": "PAMVideoAnalysis",
                "POWERTOOLS_LOGGER_LOG_EVENT": "true",
                "POWERTOOLS_LOG_LEVEL": "DEBUG"
            },
            layers=[boto3_lambda_layer, ffmpeg_layer, powertools_layer]
        )
        video_bucket.grant_read(create_still_frame_images_function)
        image_bucket.grant_write(create_still_frame_images_function)
        
        # Define the Lambda function to process each item
        transcribe_images_function = lambda_.Function(
            self, "Transcribe-Images-Function",
            code=lambda_.Code.from_asset("lambdas/transcribe_images"),
            handler="transcribe_images.lambda_handler",
            runtime=PYTHON_VERSION,
            timeout=LAMBDA_TIMEOUT,
            environment={
                "IMAGE_BUCKET": image_bucket.bucket_name,
                "ANALYSIS_TABLE": video_transcripts_table.table_name,
                "PROMPT_TABLE": prompt_table.table_name,
                "ANALYSIS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
                "POWERTOOLS_SERVICE_NAME": "pam-video-analysis",
                "POWERTOOLS_METRICS_NAMESPACE": "PAMVideoAnalysis",
                "POWERTOOLS_LOGGER_LOG_EVENT": "true",
                "POWERTOOLS_LOG_LEVEL": "DEBUG"
            },
            layers=[boto3_lambda_layer, powertools_layer]
        )
        image_bucket.grant_read(transcribe_images_function)
        video_transcripts_table.grant_write_data(transcribe_images_function)
        prompt_table.grant_read_data(transcribe_images_function)
        image_analysis_bedrock_policy = iam.PolicyStatement(
            effect = iam.Effect.ALLOW,
            actions = ['bedrock:InvokeModel',],
            resources = [
                bedrock.FoundationModel.from_foundation_model_id(self, "Claude3-Haiku", bedrock.FoundationModelIdentifier.ANTHROPIC_CLAUDE_3_HAIKU_20240307_V1_0).model_arn,
                bedrock.FoundationModel.from_foundation_model_id(self, "Claude3-Sonnet", bedrock.FoundationModelIdentifier.ANTHROPIC_CLAUDE_3_SONNET_20240229_V1_0).model_arn,
                ]
        )
        transcribe_images_function.add_to_role_policy(image_analysis_bedrock_policy)

        # Define the Lambda function to aggregate all analyses
        aggregate_segment_transcripts_function = lambda_.Function(
            self, "Aggregate-Segment-Transcripts-Function",
            code=lambda_.Code.from_asset("lambdas/aggregate_transcripts"),
            handler="aggregate_transcripts.lambda_handler",
            runtime=PYTHON_VERSION,
            timeout=LAMBDA_TIMEOUT,
            environment={
                "ANALYSIS_TABLE": video_transcripts_table.table_name,
                "PROMPT_TABLE": prompt_table.table_name,
                "AGGREGATE_MODEL_ID": "anthropic.claude-3-sonnet-20240229-v1:0",
                "POWERTOOLS_SERVICE_NAME": "pam-video-analysis",
                "POWERTOOLS_METRICS_NAMESPACE": "PAMVideoAnalysis",
                "POWERTOOLS_LOGGER_LOG_EVENT": "true",
                "POWERTOOLS_LOG_LEVEL": "DEBUG"
            },
            layers=[boto3_lambda_layer, powertools_layer]
        )
        video_transcripts_table.grant_read_write_data(aggregate_segment_transcripts_function)
        prompt_table.grant_read_data(aggregate_segment_transcripts_function)
        aggregation_bedrock_policy = iam.PolicyStatement(
            effect = iam.Effect.ALLOW,
            actions = ['bedrock:InvokeModel',],
            resources = [
                bedrock.FoundationModel.from_foundation_model_id(self, "Claude3-Haiku", bedrock.FoundationModelIdentifier.ANTHROPIC_CLAUDE_3_HAIKU_20240307_V1_0).model_arn,
                bedrock.FoundationModel.from_foundation_model_id(self, "Claude3-Sonnet", bedrock.FoundationModelIdentifier.ANTHROPIC_CLAUDE_3_SONNET_20240229_V1_0).model_arn,
                bedrock.FoundationModel.from_foundation_model_id(self, "Meta-Llama3-70b", bedrock.FoundationModelIdentifier.META_LLAMA_3_70_INSTRUCT_V1).model_arn,
                bedrock.FoundationModel.from_foundation_model_id(self, "AI21-Jamba", bedrock.FoundationModelIdentifier.AI21_J2_JAMBA_INSTRUCT_V1_0).model_arn,
                ]
        )
        aggregate_segment_transcripts_function.add_to_role_policy(aggregation_bedrock_policy)

        ######################################################
        # Define the StepFunctions steps and workflow
        ######################################################
        # initial video image extraction task
        create_still_frame_images_task = tasks.LambdaInvoke(
            self, "CreateStillFrameImagesTask",
            lambda_function=create_still_frame_images_function,
            payload=sfn.TaskInput.from_object({"Input.$": "$$"}),
            result_path="$.videotaskresult",
            # output_path="$.image_batches",
        )

        # then the distributed Map to loop through all extracted images
        transcribe_images_task = sfn.Map(
            self, "ImageBatchMap",
            max_concurrency=20,
            items_path="$.videotaskresult.Payload.image_batches",
            result_path="$.distributedmapresult",
            # output_path="$.analyses_path",
        )
        transcribe_images_task.item_processor(
            processor=tasks.LambdaInvoke(
                self, "TranscribeImagesTask",
                lambda_function=transcribe_images_function,
            )
        )
        
        # eventually the aggregation task at the end
        aggregate_segment_transcript_task = tasks.LambdaInvoke(
            self, "AggregateSegmentTranscriptTask",
            lambda_function=aggregate_segment_transcripts_function,
            input_path="$.distributedmapresult[*].Payload.analysis",
            result_path="$.final_analysis",
        )

       # Build up the process chain
        chain = create_still_frame_images_task.next(transcribe_images_task).next(aggregate_segment_transcript_task)
        
        # Define the Step Functions state machine
        state_machine = sfn.StateMachine(
            self, "VideoProcessingPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(chain),
        )
        
        ######################################################
        # Create an event bridge rule that will trigger
        #  the workflow whenever a new video file is dropped
        #  on the video S3 bucket
        ######################################################
        event_rule = events.Rule(
            self,
            "workflow trigger rule",
            description="Rule to trigger the Step Functions workflow",
            event_pattern=events.EventPattern(
                resources=[f"arn:aws:s3:::{video_bucket.bucket_name}"],
                detail_type=events.Match.equals_ignore_case("object created"),
                # filter out folders (size=0) and access logs creation events
                detail = {"object": {
                    "size": events.Match.greater_than(0),
                    "key": events.Match.anything_but_prefix("access-logs/")
                    },
                },
            ),
        )
        event_rule.add_target(
            targets.SfnStateMachine(
                machine = state_machine,
            )
        )

        ######################################################
        # Store relevant items as CFN Output 
        ######################################################
        CfnOutput(self, "videobucket", value=video_bucket.bucket_name) 
        CfnOutput(self, "imagebucket", value=image_bucket.bucket_name) 
        CfnOutput(self, "analysistable", value=video_transcripts_table.table_name) 
        CfnOutput(self, "prompttable", value=prompt_table.table_name) 
        
        
    ############################################
    # Helper functions to package Lambda layers
    ############################################
    def __create_layer_from_pip(self, layer_name, description: str, version: str) -> lambda_.LayerVersion:
        requirements_file = f"lambdas/layers/{layer_name}-layer/requirements.txt"  
        output_dir = f"lambdas/layers/{layer_name}-layer/.build"  # 👈🏽 a temporary directory to store the dependencies

        if not os.environ.get("SKIP_PIP"):
            # 👇🏽 download the dependencies and store them in the output_dir
            subprocess.check_call(f"pip install -r {requirements_file} -t {output_dir}/python".split())

        layer_id = f"{layer_name}-lambda-layer"  # 👈🏽 a unique id for the layer
        layer_code = lambda_.Code.from_asset(output_dir, exclude=["python/bin/*"])  # 👈🏽 import the dependencies / code

        my_layer = lambda_.LayerVersion(
            self,
            layer_id,
            code=layer_code,
            compatible_runtimes=[PYTHON_VERSION],
            description=description,
            layer_version_name=f"{layer_name}-layer-{version}"
        )
        return my_layer

    def __create_layer_from_shell(self, layer_name, description: str, version: str) -> lambda_.LayerVersion:
        # requirements_file = f"lambdas/layers/{layer_name}-layer/requirements.txt"  
        # default to shell script for Mac/Linux users
        layer_dir = f"lambdas/layers/{layer_name}-layer/"
        build_command = f"sh {layer_dir}/build.sh"
        if platform.system() == "Windows":
            build_command = f"powershell.exe {layer_dir}/build.ps1"

        if not os.environ.get("SKIP_BUILD"):
            # 👇🏽 package the executable in the layer directory
            subprocess.check_call(f"{build_command}".split())

        layer_id = f"{layer_name}-lambda-layer"  # 👈🏽 a unique id for the layer
        layer_code = lambda_.Code.from_asset(f"{layer_dir}/{layer_name}.zip")  # 👈🏽 import the packaged archive

        my_layer = lambda_.LayerVersion(
            self,
            layer_id,
            code=layer_code,
            compatible_runtimes=[PYTHON_VERSION],
            description=description,
            layer_version_name=f"{layer_name}-layer-{version}"
        )
        return my_layer
