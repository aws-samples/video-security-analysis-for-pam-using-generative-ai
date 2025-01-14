import boto3
import os
import json
import datetime
from botocore.config import Config
import traceback
import re
import datetime
from typing import List
from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging import correlation_paths

from boto3.dynamodb.types import TypeDeserializer

logger = Logger()

logger.debug(json.dumps(dict(os.environ), indent=4))
region = os.environ['AWS_REGION']

config = Config(read_timeout=1000, region_name=region)
bedrock_runtime = boto3.client("bedrock-runtime", config=config)
ddb = boto3.client("dynamodb", config=config)
analysis_table = os.environ["ANALYSIS_TABLE"]
prompt_table = os.environ["PROMPT_TABLE"]

date_timestamp = (datetime.datetime.now()).strftime("%Y-%m-%d_%H:%M:%S")

# extract prompt from DynamoDB table
def build_prompt() -> tuple[str, str] | None:
    logger.debug(f"###### Retrieving aggregate prompt from DynamoDB table '{prompt_table}' ######")
    try:
        version_zero = ddb.get_item(
            TableName=prompt_table,
            Key={
                "PromptID": {"S": "aggregate-prompt"},
                "VersionID": {"S": "v0"}
            }
        )
        latest_version = "v" + version_zero["Item"]["Latest"]["N"]
        results = ddb.get_item(
            TableName=prompt_table,
            Key={
                "PromptID": {"S": "aggregate-prompt"},
                "VersionID": {"S": latest_version}
            }
        )
        prompt_item = results["Item"]

        ##### Prompt element 1: Task context
        # Give Claude context about the role it should take on or what goals and overarching tasks you want it to undertake with the prompt.
        # It's best to put context early in the body of the prompt.
        TASK_CONTEXT = f"{prompt_item['TASK_CONTEXT']['S']}"

        ##### Prompt element 2: Tone context
        # If important to the interaction, tell Claude what tone it should use.
        # This element may not be necessary depending on the task.
        TONE_CONTEXT = f"{prompt_item['TONE_CONTEXT']['S']}"

        ##### Prompt element 3: Detailed task description and rules
        # Expand on the specific tasks you want Claude to do, as well as any rules that Claude might have to follow.
        # This is also where you can give Claude an "out" if it doesn't have an answer or doesn't know.
        # It's ideal to show this description and rules to a friend to make sure it is laid out logically and that any ambiguous words are clearly defined.
        TASK_DESCRIPTION = f"{prompt_item['TASK_DESCRIPTION']['S']}"
        
        ##### Prompt element 4: Examples
        # Provide Claude with at least one example of an ideal response that it can emulate. Encase this in <example></example> XML tags. Feel free to provide multiple examples.
        # If you do provide multiple examples, give Claude context about what it is an example of, and enclose each example in its own set of XML tags.
        # Examples are probably the single most effective tool in knowledge work for getting Claude to behave as desired.
        # Make sure to give Claude examples of common edge cases. If your prompt uses a scratchpad, it's effective to give examples of how the scratchpad should look.
        # Generally more examples = better.
        EXAMPLES = f"{prompt_item['EXAMPLES']['S']}"

        ##### Prompt element 5: Input data to process
        # If there is data that Claude needs to process within the prompt, include it here within relevant XML tags.
        # Feel free to include multiple pieces of data, but be sure to enclose each in its own set of XML tags.
        # This element may not be necessary depending on task. Ordering is also flexible.
        INPUT_DATA = f"{prompt_item['INPUT_DATA']['S']}"

        ##### Prompt element 6: Immediate task description or request #####
        # "Remind" Claude or tell Claude exactly what it's expected to immediately do to fulfill the prompt's task.
        # This is also where you would put in additional variables like the user's question.
        # It generally doesn't hurt to reiterate to Claude its immediate task. It's best to do this toward the end of a long prompt.
        # This will yield better results than putting this at the beginning.
        # It is also generally good practice to put the user's query close to the bottom of the prompt.
        IMMEDIATE_TASK = f"{prompt_item['IMMEDIATE_TASK']['S']}"

        ##### Prompt element 7: Precognition (thinking step by step)
        # For tasks with multiple steps, it's good to tell Claude to think step by step before giving an answer
        # Sometimes, you might have to even say "Before you give your answer..." just to make sure Claude does this first.
        # Not necessary with all prompts, though if included, it's best to do this toward the end of a long prompt and right after the final immediate task request or description.
        PRECOGNITION = f"{prompt_item['PRECOGNITION']['S']}"

        ##### Prompt element 8: Output formatting
        # If there is a specific way you want Claude's response formatted, clearly tell Claude what that format is.
        # This element may not be necessary depending on the task.
        # If you include it, putting it toward the end of the prompt is better than at the beginning.
        OUTPUT_FORMATTING = f"{prompt_item['OUTPUT_FORMATTING']['S']}"

        ##### Prompt element 9: Prefilling Claude's response (if any)
        # A space to start off Claude's answer with some prefilled words to steer Claude's behavior or response.
        # If you want to prefill Claude's response, you must put this in the `assistant` role in the API call.
        # This element may not be necessary depending on the task.
        PREFILL = f"{prompt_item['PREFILL']['S']}"

        prompt = ""
        if TASK_CONTEXT:
            prompt += f"""{TASK_CONTEXT}"""
        if TONE_CONTEXT:
            prompt += f"""\n\n{TONE_CONTEXT}"""
        if TASK_DESCRIPTION:
            prompt += f"""\n\n{TASK_DESCRIPTION}"""
        if EXAMPLES:
            prompt += f"""\n\n{EXAMPLES}"""
        if INPUT_DATA:
            prompt += f"""\n\n{INPUT_DATA}"""
        if IMMEDIATE_TASK:
            prompt += f"""\n\n{IMMEDIATE_TASK}"""
        if PRECOGNITION:
            prompt += f"""\n\n{PRECOGNITION}"""
        if OUTPUT_FORMATTING:
            prompt += f"""\n\n{OUTPUT_FORMATTING}"""
        if PREFILL:
            prompt += f"""\n\n{PREFILL}"""

        prompt_version = f"aggregate-v{version_zero["Item"]["Latest"]["N"]}"
        return prompt, prompt_version
    
    except Exception as e:
        logger.error(f"Something got wrong: {e}")
        logger.error(traceback.format_exc())
        return None


# Create the function to submit the compiled prompt and images to Bedrock
def summarize_analysis(
    model_id: str,
    analysis_history: List[str],
    max_tokens: int = 4096,
    temperature: float = 0,
    top_p: float = 0.999,
    top_k: int = 250,
) -> tuple[str, str] | None:
    logger.debug("###### Sending to Bedrock ######")
    prompt_version = ""
    try:
        # Base inference parameters to use.
        inference_config = {
            "maxTokens": max_tokens,
            "temperature": temperature,
            "topP": top_p,
        }
        # Additional inference parameters to use when needed
        additional_model_fields = None
        if (model_id.__contains__("anthropic.claude")):
            additional_model_fields = {"top_k": top_k}

        history = []
        for hist in analysis_history:
            history.append({"text": hist})
        messages = [{"role": "user", "content": history}]
        
        # Get system prompt
        prompt, prompt_version = build_prompt()
        system_prompts = [{"text": prompt}]
        logger.debug(f"system prompts={json.dumps(system_prompts[0])}")
        logger.debug(f"prompt version='{prompt_version}'")
        
        # Send the message.
        resp = bedrock_runtime.converse(
            modelId=model_id,
            messages=messages,
            system=system_prompts,
            inferenceConfig=inference_config,
            additionalModelRequestFields=additional_model_fields,
        )
        result = resp["output"]["message"]
        logger.debug(f"Bedrock's response={result}")
        return result["content"][0]["text"], prompt_version
    
    except Exception as e:
        logger.error(f"Error calling Bedrock's Converse API: {e}")
        logger.error(traceback.format_exc())
        return "Empty summary due to aggregation error - check out Lambda logs in CloudWatch", prompt_version


# NOT USED (analysis history is collected from Lambda fonction's input)
def load_analysis_history(video_id: str) -> List[str] | None:
    logger.debug("###### Reading analysis history from DynamoDB ######")
    try:
        # deserializer = TypeDeserializer()
        analysis_history = []
        last_evaluated_key = None

        while True:
            if last_evaluated_key:
                results = ddb.query(
                    TableName=analysis_table,
                    KeyConditionExpression='VideoID = :video_id AND begins_with(SequenceID, :sequence_id)',
                    ExpressionAttributeValues={
                        ':video_id': {'S': video_id},
                        ':sequence_id': {'S': 'seq'}
                    },
                    ExclusiveStartKey=last_evaluated_key
                )
            else:
                results = ddb.query(
                    TableName=analysis_table,
                    KeyConditionExpression='VideoID = :video_id AND begins_with(SequenceID, :sequence_id)',
                    ExpressionAttributeValues={
                        ':video_id': {'S': video_id},
                        ':sequence_id': {'S': 'seq'}
                    }
                )
            
            last_evaluated_key = results.get('LastEvaluatedKey')
            # analysis_history.extend([deserializer.deserialize(item) for item in results['Items']])
            analysis_history.append(item for item in results['Items'])

            if not last_evaluated_key:
                break

        '''
        analysis_history will be like :
        [
            {
                'Analysis': {'S': '<analysis>\n1. The IT Systems Administrator opens the Paint application.\n2. The IT Systems Administrator types the text "Hello World" in the Paint application.\n3. The IT Systems Administrator saves the file as "hello-world" and file type as "PNG (*.png)".\n</analysis>'}, 
                'VideoID': {'S': 'video-1234'}, 
                'SequenceID': {'S': 'sequence-1'}, 
                'Created': {'S': '2024-08-12_21:18:16'}
            }, 
            {
                'Analysis': {'S': '<analysis>\n1. The IT Systems Administrator opens the Paint application.\n2. The IT Systems Administrator types the text "Hello World :)" in the Paint application.\n3. The IT Systems Administrator saves the file as "hello-world" with the PNG (*.png) file type.\n4. The IT Systems Administrator closes the Save As dialog and returns to the default Windows desktop with no active applications or windows visible.\n</analysis>'}, 
                'VideoID': {'S': 'video-1234'}, 
                'SequenceID': {'S': 'sequence-2'}, 
                'Created': {'S': '2024-08-12_21:26:27'}
            }, 
            {
                'Analysis': {'S': '<analysis>\n1. The image shows the Windows 10 desktop with no active applications or windows open.\n2. The desktop background is a solid blue color with the Windows logo prominently displayed.\n</analysis>'}, 
                'VideoID': {'S': 'video-1234'}, 
                'SequenceID': {'S': 'sequence-3'}, 
                'Created': {'S': '2024-08-12_21:35:41'}
            }
        ]
        '''
        # FIX - extract the content
        '''
        history = []
        for hist in analysis_history:
            logger.debug(f"hist={hist}")
            hist_json = json.loads(hist)
            history.append(hist_json['Analysis']['S'])
        '''

        return analysis_history
    
    except Exception as e:
        logger.error(f"Error loading data from DynamoDB: {e}")
        logger.error(traceback.format_exc())
        return None


def store_full_analysis(video_id: str, video_s3_uri: str, video_url: str, analysis: str, prompt_version: str) -> None:
    logger.debug("###### Storing full analysis in DynamoDB ######")
    try:
        ddb.put_item(
            TableName=analysis_table,
            Item={
                "VideoID": {"S": video_id},
                "SequenceID": {"S": f"{prompt_version}#full"},
                "Analysis": {"S": analysis},
                "VideoS3URI": {"S": video_s3_uri},
                "VideoURL": {"S": video_url},
                "Created": {"S": date_timestamp}
            })
    
    except Exception as e:
        logger.error(f"Error storing analysis in DynamoDB: {e}")
        logger.error(traceback.format_exc())
        
    finally:
        return None
