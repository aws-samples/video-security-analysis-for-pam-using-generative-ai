import json
import os
import boto3, botocore
from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from lib import transcribe_images_converse as ai_lib # type: ignore

logger = Logger()
metrics = Metrics()

# Create an S3 client
aws_region = os.environ['AWS_REGION']
s3_client = boto3.client('s3', region_name=aws_region)

@logger.inject_lambda_context
@metrics.log_metrics
def lambda_handler(event, context):
    logger.debug(f"Boto3 version:{boto3.__version__}")
    logger.debug(f"Botocore version:{botocore.__version__}")
    logger.debug('## ENVIRONMENT VARIABLES')
    logger.debug(json.dumps(dict(os.environ), indent=4))
    logger.debug('## EVENT')
    logger.debug(event)
    logger.debug('## CONTEXT')
    logger.debug(context)

    region = os.environ['AWS_REGION']
    ######################################## INPUT VARIABLES ########################################
    timelapse = 1
    response = ""

    image_bucket_name = os.environ["IMAGE_BUCKET"]
    path_to_image_files = event["image_path"]

    ''' some examples of LLMs that can be used for the aggregation
    models = ["anthropic.claude-3-haiku-20240307-v1:0",
            "anthropic.claude-3-sonnet-20240229-v1:0",
    ]
    '''    
    model_id = os.environ["ANALYSIS_MODEL_ID"]

    batch_info = event["batch_info"]
    video_id = batch_info["video_id"]
    video_s3_uri = batch_info["video_s3_uri"]
    video_url = batch_info["video_url"]
    sequence_id = batch_info["sequence_id"]
    image_list = event["image_list"]
    number_of_images = len(image_list)

    logger.debug(f"Analyzing content from location '{path_to_image_files}' on S3 bucket '{image_bucket_name}'")
    ######################################## BUILD PAYLOAD TO BE SENT TO BEDROCK ########################################
    
    payload_content = ai_lib.create_content(image_bucket_name, path_to_image_files, image_list)
    ######################################## SEND TO BEDROCK ########################################
    # build the prompt
    history = "" # no history
    prompt, prompt_version = ai_lib.build_prompt(history, timelapse, number_of_images)
    analysis = ai_lib.analyse_images(model_id=model_id, content=payload_content, prompt=prompt, max_tokens = 4096, temperature = 0, top_p = 0, top_k = 250)
    # store the sequence analysis in DynamoDB
    ai_lib.store_analysis(video_id, sequence_id, analysis, prompt_version)

    if analysis.startswith("Empty analysis"):
        logger.info(f"Image analysis for video with ID '{video_id}' is incomplete, failed analysis of sequence with ID '{sequence_id}'... check the logs for errors")
        metrics.add_metric(name="ImageAnalysisError", unit=MetricUnit.Count, value=1)
    else:
        logger.info(f"###### Analysis done for sequence with ID '{sequence_id}' of video with ID '{video_id}' ######")
        logger.debug(f"Analysis of video with ID#{video_id}: \n{analysis}")

    
    # Return the handling result
    return {
        "event": event,
        "status": "OK",
        "message": "Images Analysed!",
        "analysis": { 
            "video_id": video_id,
            "video_s3_uri": video_s3_uri,
            "video_url": video_url,
            "sequence_id": sequence_id,
            "description": analysis
        }
    }
    
# for local debugging purposes only
if __name__ == "__main__":
    print(f"Boto3 version:{boto3.__version__}")
    print(f"Botocore version:{botocore.__version__}")
    event = {
        "batch_info": { 
            "video_id": "video-1234", 
            "sequence_id": "sequence-1"
        },
        "image_path": "hello-world",
        "image_list": [
            "0001.png", "0002.png", "0003.png", "0004.png", "0005.png", "0006.png", "0007.png", "0008.png", "0009.png", "0010.png",
            "0011.png", "0012.png", "0013.png", "0014.png", "0015.png", "0016.png", "0017.png", "0018.png", "0019.png", "0020.png"
        ] 
    }
    context = {
        "aws_request_id": "c85bef1e-1728-4e8a-84d8-22912f542c73",
        "log_group_name": "aws/lambda/ImageAnalysisFunctionCE189004",
        "log_stream_name":"$LATEST",
        "function_name": "ImageAnalysisFunctionCE189004",
        "memory_limit_in_mb": 128,
        "function_version": "$LATEST",
        "invoked_function_arn": "arn:aws:lambda:us-east-1:012345678912:function:ImageAnalysisFunctionCE189004",
        "client_context": None,
        "identity": None
    }
    lambda_handler(event, context)
    