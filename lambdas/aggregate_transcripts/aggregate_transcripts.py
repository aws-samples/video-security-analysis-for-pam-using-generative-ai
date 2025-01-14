import json
import os
from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit

from lib import summary_converse as ai_lib # type: ignore

logger = Logger()
metrics = Metrics()

@logger.inject_lambda_context
@metrics.log_metrics
def lambda_handler(event, context):
    logger.debug('## ENVIRONMENT VARIABLES')
    logger.debug(json.dumps(dict(os.environ), indent=4))
    logger.debug('## EVENT')
    logger.debug(event)
    logger.debug('## CONTEXT')
    logger.debug(context)

    '''
    input from previous step (distributed map) looks like the following:
    [
        {
            'video_id': 'video-1234', 
            'sequence_id': 'sequence-1', 
            'description': '<analysis>\n1. The IT Systems Administrator opens the Paint application.\n2. The IT Systems Administrator types the text "He" in the Paint application.\n3. The IT Systems Administrator types the text "Hello" in the Paint application.\n4. The IT Systems Administrator types the text "Hello World!" in the Paint application.\n5. The IT Systems Administrator adds a smiley face ":)" to the end of the text "Hello World!".\n</analysis>'
        }, 
        {
            'video_id': 'video-1234', 
            'sequence_id': 'sequence-2', 
            'description': '<analysis>\n1. The IT Systems Administrator opens the Paint application.\n2. The IT Systems Administrator types the text "Hello World :)" in the Paint application.\n3. The IT Systems Administrator appears to be saving the file, as the "Save As" dialog box is displayed, but the specific save action has not been confirmed.\n4. The IT Systems Administrator navigates to the local disk drive and selects the file name "hello-world" with the file type "PNG (*.png)".\n5. The IT Systems Administrator clicks the "Save" button to save the file.\n6. The IT Systems Administrator closes the Paint application, returning to the Windows desktop.\n</analysis>'
        }, 
        {
            'video_id': 'video-1234', 
            'sequence_id': 'sequence-3', 
            'description': '<analysis>\n1. The image shows the Windows 10 desktop with no active applications or windows open.\n2. The desktop background is a solid blue color with the Windows logo prominently displayed.\n</analysis>'
        }
    ]
    '''
    video_id = event[0]["video_id"]
    video_s3_uri = event[0]["video_s3_uri"]
    video_url = event[0]["video_url"]
    history = []
    for input in event:
        history.append(input['description'])        
    # alternatively, load the analysis history from Dynamo DB
    # analysis_history = ai_lib.load_analysis_history(video_id)
    ''' some examples of LLMs that can be used for the aggregation
    models = ["anthropic.claude-3-sonnet-20240229-v1:0",
            "meta.llama3-70b-instruct-v1:0",
            "ai21.jamba-instruct-v1:0",
    ]
    '''    
    model_id = os.environ["AGGREGATE_MODEL_ID"]
    # pass the history to Bedrock and get a summary out of it 
    full_analysis, prompt_version = ai_lib.summarize_analysis(model_id, history, max_tokens = 4096, temperature = 0, top_p = 0, top_k = 250)
    # store the full analysis in DynamoDB
    ai_lib.store_full_analysis(video_id, video_s3_uri, video_url, full_analysis, prompt_version)

    if full_analysis.startswith("Empty summary"):
        logger.info(f"Analysis for video with ID '{video_id}' could not be completed, check logs for errors")
        metrics.add_metric(name="AggregationError", unit=MetricUnit.Count, value=1)
    else:
        logger.info(f"Analysis for video with ID '{video_id}' is now available on DynamoDB")
        metrics.add_metric(name="FullAnalysis", unit=MetricUnit.Count, value=1)
    
    # Return the handling result
    return {
        "event": event,
        "status": "OK",
        "message": "Analyses aggregated!",
        "aggregate analysis": full_analysis
    }