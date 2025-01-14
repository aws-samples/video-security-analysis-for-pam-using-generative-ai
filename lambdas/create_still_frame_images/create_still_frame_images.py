import os, subprocess
import shutil, shlex
import boto3, botocore
from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import S3Event

logger = Logger()
metrics = Metrics()

# Create an S3 client
aws_region = os.environ['AWS_REGION']
s3_client = boto3.client('s3', region_name=aws_region)

@logger.inject_lambda_context
@metrics.log_metrics
def lambda_handler(event: S3Event, context: LambdaContext):
    logger.debug(f"Boto3 version:{boto3.__version__}")
    logger.debug(f"Botocore version:{botocore.__version__}")
    logger.debug('## ENVIRONMENT VARIABLES')
    logger.debug(os.environ['AWS_LAMBDA_LOG_GROUP_NAME'])
    logger.debug(os.environ['AWS_LAMBDA_LOG_STREAM_NAME'])
    logger.debug('## EVENT')
    logger.debug(event)
    logger.debug('## CONTEXT')
    logger.debug(context)

    video_bucket = os.environ["VIDEO_BUCKET"]
    image_bucket = os.environ["IMAGE_BUCKET"]

    # Extract which (video) file drop on S3 triggered the workflow from the event
    ''' Event coming from S3 will look like the following
    {
        "version": "0",
        "id": "17793124-05d4-b198-2fde-7ededc63b103",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "account": "123456789012",
        "time": "2021-11-12T00:00:00Z",
        "region": "eu-central-1",
        "resources": ["arn:aws:s3:::example-bucket"],
        "detail": {
            "version": "0",
            "bucket": {
                "name": "example-bucket"
            },
            "object": {
                "key": "example-key",
                "size": 5,
                "etag": "b1946ac92492d2347c6235b4d2611184",
                "version-id": "IYV3p45BT0ac8hjHg1houSdS1a.Mro8e",
                "sequencer": "00617F08299329D189"
            },
            "request-id": "N4N7GDK58NMKJ12R",
            "requester": "123456789012",
            "source-ip-address": "1.2.3.4",
            "reason": "PutObject"
        }
    }
    '''
    video_object_key = event["Input"]["Execution"]["Input"]["detail"]["object"]["key"]
    # aws_region = event["Input"]["Execution"]["Input"]["region"]
    video_s3_uri = f"s3://{video_bucket}/{video_object_key}"
    if (aws_region == "us-east-1"):
        video_url = f"https://{video_bucket}.s3.amazonaws.com/{video_object_key}"
    else:
        video_url = f"https://{video_bucket}.s3.{aws_region}.amazonaws.com/{video_object_key}"

    # replace '/' with '-'
    video_id = video_object_key.replace("/", "-")
    logger.info(f"Starting processing of video file '{video_s3_uri}' => VideoID='{video_id}'")
    
    # Extract still frame images from the video
    # Use FFmpeg to create a PNG file for every second of the video
    local_video_path = '/tmp/video.mp4'
    # Download the video file from the source S3 bucket
    s3_client.download_file(video_bucket, video_object_key, local_video_path)        
    tmp_image_dir = '/tmp/images'
    if not os.path.exists(tmp_image_dir):
        os.makedirs(tmp_image_dir)
    ffmpeg_cmd = f'ffmpeg -i {shlex.quote(local_video_path)} -vf fps=1 {shlex.quote(f"{tmp_image_dir}/%05d.png")}'
    logger.debug(f"Executing the following ffmpeg command: {ffmpeg_cmd}")
    # subprocess.run(shlex.split(ffmpeg_cmd), check=True)
    subprocess.check_call(shlex.split(ffmpeg_cmd))

    # Upload the still frame images to the destination S3 bucket
    # 'image_path' and 'image_list' are expected to look like this
    '''
    image_path = "hello-world"
    image_list = [
        "0001.png", "0002.png", "0003.png", "0004.png", "0005.png", "0006.png", "0007.png", "0008.png", "0009.png", "0010.png",
        "0011.png", "0012.png", "0013.png", "0014.png", "0015.png", "0016.png", "0017.png", "0018.png", "0019.png", "0020.png",
        "0021.png", "0022.png", "0023.png", "0024.png", "0025.png", "0026.png", "0027.png", "0028.png", "0029.png", "0030.png",
        "0031.png", "0032.png", "0033.png", "0034.png", "0035.png", "0036.png", "0037.png", "0038.png", "0039.png", "0040.png",
        "0041.png"
    ]
    '''
    image_list = []
    image_path = video_object_key
    for filename in sorted(os.listdir(tmp_image_dir)): # force alphabetical order to have images in the right sequential order
        image_list.append(filename)
        local_image_path = os.path.join(tmp_image_dir, filename)
        image_key = f'{image_path}/{filename}'
        s3_client.upload_file(local_image_path, image_bucket, image_key)

    # Clean up temporary files
    os.remove(local_video_path)
    shutil.rmtree(tmp_image_dir)    
        
    # At the moment of writing this, Bedrock can process up to 20 images at a time
    #  so, return the images list in batches of up to 20 images 
    image_batch_size = 20
    image_batches = []
    for i in range(0, len(image_list), image_batch_size):
        image_batch = image_list[i:i+image_batch_size]
        image_batches.append(image_batch)
    
    logger.info(f"Finished extracting still images from video file '{video_s3_uri}' => VideoID='{video_id}'. \nExtracted images can be found at s3://{image_bucket}/{image_path}/")
    metrics.add_metric(name="IngestedPAMVideos", unit=MetricUnit.Count, value=1)
    
    return {
        "event": event,
        "status": "OK",
        "message": "Video processed!", 
        "image_batches": [
            {  
                "batch_info": { 
                    "video_id": video_id, 
                    "video_s3_uri": video_s3_uri,
                    "video_url": video_url,
                    "sequence_id": f"sequence-{k+1}"
                },
                "image_path": image_path,
                "image_list": image_batch
            } for k, image_batch in enumerate(image_batches)
        ]
    }
    # Example of 'image_batches' below
    '''        
        "image_batches": [
            {  
                "batch_info": { 
                    "video_id": video_id, 
                    "video_s3_uri": video_s3_uri,
                    "video_url": video_url,
                    "sequence_id": "sequence-1"
                },
                "image_path": image_path,
                "image_list": [
                    "0001.png", "0002.png", "0003.png", "0004.png", "0005.png", "0006.png", "0007.png", "0008.png", "0009.png", "0010.png",
                    "0011.png", "0012.png", "0013.png", "0014.png", "0015.png", "0016.png", "0017.png", "0018.png", "0019.png", "0020.png"
                ] 
            },
            {
                "batch_info": { 
                    "video_id": video_id, 
                    "video_s3_uri": video_s3_uri,
                    "video_url": video_url,
                    "sequence_id": "sequence-2"
                },
                "image_path": image_path,
                "image_list": [
                    "0021.png", "0022.png", "0023.png", "0024.png", "0025.png", "0026.png", "0027.png", "0028.png", "0029.png", "0030.png",
                    "0031.png", "0032.png", "0033.png", "0034.png", "0035.png", "0036.png", "0037.png", "0038.png", "0039.png", "0040.png"
                ] 
            },
            {
                "batch_info": { 
                    "video_id": video_id, 
                    "video_s3_uri": video_s3_uri,
                    "video_url": video_url,
                    "sequence_id": "sequence-3"
                },
                "image_path": image_path,
                "image_list": [
                    "0041.png"
                ] 
            }
        ]
    '''