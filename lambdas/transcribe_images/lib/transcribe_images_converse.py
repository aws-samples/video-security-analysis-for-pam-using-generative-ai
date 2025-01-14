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

logger = Logger()

logger.debug(json.dumps(dict(os.environ), indent=4))
region = os.environ['AWS_REGION']

config = Config(read_timeout=1000, region_name=region)
s3 = boto3.client("s3", config=config)
bedrock_runtime = boto3.client("bedrock-runtime", config=config)

ddb = boto3.client("dynamodb", config=config)
analysis_table = os.environ["ANALYSIS_TABLE"]
prompt_table = os.environ["PROMPT_TABLE"]

date_timestamp = (datetime.datetime.now()).strftime("%Y-%m-%d_%H:%M:%S")

######################################## DEFINE FUNCTIONS ########################################
# Create function to build the prompt
def build_prompt(response_history: str = "", timelapse: int = 1, number_of_images: int = 20) -> tuple[str, str] | None:
    
    logger.debug(f"###### Retrieving aggregate prompt from DynamoDB table '{prompt_table}' ######")
    try:
        version_zero = ddb.get_item(
            TableName=prompt_table,
            Key={
                "PromptID": {"S": "analysis-prompt"},
                "VersionID": {"S": "v0"}
            }
        )
        latest_version = "v" + version_zero["Item"]["Latest"]["N"]
        results = ddb.get_item(
            TableName=prompt_table,
            Key={
                "PromptID": {"S": "analysis-prompt"},
                "VersionID": {"S": latest_version}
            }
        )
        prompt_item = results["Item"]

        prompt_version = f"analysis-v{version_zero["Item"]["Latest"]["N"]}"
        logger.debug(f"Analysis prompt:\n---\n{json.dumps(prompt_item)}\n---")

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

        # Print full prompt
        logger.debug("--------------------------- Full prompt with variable substitutions ---------------------------")
        logger.debug(prompt)
        return prompt, prompt_version
    
    except Exception as e:
        logger.error(f"Something went wrong: {e}")
        logger.error(traceback.format_exc())
        return None

def create_content(image_bucket_name: str, image_path: str, image_list: List[str]) -> List[dict]:
    payload_content_list = []
    logger.debug("###### Reading images from S3 ######")

    total_num_images = len(image_list)
    logger.debug(f"batch contains {total_num_images} images")
    payload_content_list.append({"text": f"reading images in '{','.join(image_list)}'"})
    # Loop through the image files and build the payload for Bedrock's Converse API
    for i, image_file in enumerate(image_list):
        object_key = os.path.join(image_path,image_file)
        logger.debug(f"reading content of image at {object_key}")
        payload_content_list.append(
            {
                "image": {
                    "format": "png",
                    "source": {
                        "bytes": s3.get_object(
                            Bucket=image_bucket_name, Key=object_key
                        )["Body"].read()
                    },
                }
            }
        )
    return payload_content_list


# Create the function to submit the compiled prompt and images to Bedrock
def analyse_images(
    model_id: str,
    content: List[dict],
    prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0,
    top_p: float = 0.999,
    top_k: int = 250,
):
    logger.debug("###### Sending to Bedrock ######")
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

        messages = [{"role": "user", "content": content}]
        system_prompts = [{"text": prompt}]

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
        return result["content"][0]["text"]
    except Exception as e:
        logger.error(f"Error calling Bedrock's Converse API: {e}")
        logger.error(traceback.format_exc())
        return "Empty analysis due to image analysis error - check out Lambda logs in CloudWatch"


def store_analysis(video_id: str, sequence_id: str, analysis: str, prompt_version: str) -> None:
    logger.debug("###### Sending to Bedrock ######")
    try:
        ddb.put_item(
            TableName=analysis_table,
            Item={
                "VideoID": {"S": video_id},
                "SequenceID": {"S": f"{prompt_version}#{sequence_id}"},
                "Analysis": {"S": analysis},
                "Created": {"S": date_timestamp}
            })

    except Exception as e:
        logger.error(f"Error storing analysis in DynamoDB: {e}")
        logger.error(traceback.format_exc())

    finally:
        return None
    