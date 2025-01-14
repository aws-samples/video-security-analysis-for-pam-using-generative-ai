import streamlit as st
import boto3
from boto3.dynamodb.conditions import Key
import pandas as pd
import re
from datetime import datetime
from botocore.exceptions import ClientError
from urllib.parse import urlparse
import json

st.set_page_config(page_title="Privileged Access Video Security Analysis")

#Connect to DynamoDB
dynamodb = boto3.resource('dynamodb')

# import json object from cfn_outputs.json file and create variable for llm_prompt_table
with open('../cfn_outputs.json') as f:
    cfn_outputs = json.load(f)
    llm_prompt_table_name = cfn_outputs['PAMVideoAnalysis']['prompttable']
    transcript_table_name = cfn_outputs['PAMVideoAnalysis']['analysistable']


#update once parameter store is set up
prompt_table = dynamodb.Table(llm_prompt_table_name) 
transcript_table = dynamodb.Table(transcript_table_name)

def create_presigned_url(bucket_name, object_name, expiration=3600):
    """Generate a presigned URL to share an S3 object"""
    s3_client = boto3.client('s3')
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        print(e)
        return None
    return response

def get_s3_details_from_uri(s3_uri):
    """Extract bucket and key from S3 URI"""
    parsed_uri = urlparse(s3_uri)
    bucket = parsed_uri.netloc
    key = parsed_uri.path.lstrip('/')
    return bucket, key

def invoke_bedrock(user_message, model_id="anthropic.claude-3-haiku-20240307-v1:0"):
    bedrock = boto3.client(service_name='bedrock-runtime')
    
    print("invoke_bedrock")

    messages = [
        {"role": "user", "content": user_message}
    ]
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": messages,
        "temperature": 0.5,
        "top_p": 0.9,
    })
    
    try:
        response = bedrock.invoke_model(
            body=body,
            modelId=model_id,
            accept='application/json',
            contentType='application/json'
        )
        response_body = json.loads(response.get('body').read())
        return response_body.get('content')[0].get('text')
    except ClientError as e:
        print(f"An error occurred: {e}")
        return None

# New function to fetch security analysis prompts
def fetch_security_analysis_prompts():
    # Scan the entire table
    response = prompt_table.scan()
    security_prompts = response['Items']

    # Format the prompts for easier use
    formatted_prompts = []
    for prompt in security_prompts:
        # Skip 'VersionID' that equal 0 and 'PromptID' that start with 'analysis-prompt' or 'aggregate-prompt'
        if prompt['VersionID'] == 'v0' or prompt['PromptID'].startswith('analysis-prompt') or prompt['PromptID'].startswith('aggregate-prompt'):
            continue

        prompt_text = ""
        for key, value in prompt.items():
            if key not in ['PromptID', 'VersionID', 'Latest']:
                if len(value.strip()) > 0:
                    prompt_text += value
        
        formatted_prompts.append({
            'PromptID': prompt['PromptID'] + '_' + prompt['VersionID'],
            'PromptText': prompt_text.strip()
        })

    return formatted_prompts

def show():
    st.title("View Transcripts")

    # Fetch all transcripts
    response = transcript_table.scan()
    items = response['Items']

    # Convert to DataFrame and sort
    df = pd.DataFrame(items)
    df['Created'] = pd.to_datetime(df['Created'], format='%Y-%m-%d_%H:%M:%S')
    # df['Created'] = pd.to_datetime(df['Created'])
    df = df.sort_values(['VideoID', 'SequenceID', 'Created'], ascending=[True, False, False])
    # Filter the DataFrame to include only rows where SequenceID starts with 'aggregate'
    aggregate_df = df[df['SequenceID'].str.startswith('aggregate')]


    # Display selectable list of aggregate transcripts
    selected_transcript = st.selectbox(
        "Select a transcript",
        aggregate_df.apply(lambda row: f"{row['VideoID']} - {row['SequenceID']} - {row['Created'].strftime('%Y-%m-%d %H:%M:%S')}", axis=1),
        index=0
    )

    if selected_transcript:
        # Extract VideoID from the selected transcript
        selected_video_id = selected_transcript.split(' - ')[0]
        
        # Fetch the selected row from the DataFrame
        selected_row = aggregate_df[aggregate_df['VideoID'] == selected_video_id].iloc[0]
        
        # Display transcript details
        st.subheader("Transcript Details")
        st.write(f"Video File Name: {selected_row['VideoID']}")
        st.write(f"Version Number: {selected_row['SequenceID']}")
        st.write(f"Date and Time of Processing: {selected_row['Created']}")

        # Display the aggregated transcript
        st.subheader("Aggregated Transcript")
        st.text_area("Transcript", selected_row['Analysis'], height=300, disabled=True)

        # Option to view individual transcripts
        with st.expander("View Individual Transcripts"):
            individual_transcripts = transcript_table.query(
                KeyConditionExpression=Key('VideoID').eq(selected_row['VideoID']) & Key('SequenceID').begins_with('analysis')
                )['Items']
            if individual_transcripts:
                for transcript in individual_transcripts:
                    st.write(f"Sequence ID: {transcript['SequenceID']}")
                    st.text_area(f"Transcript {transcript['SequenceID']}", transcript['Analysis'], height=150, disabled=True)
            else:
                st.info("No individual transcripts found for this video.")

        st.header("Security Analysis")
            
        # Fetch security analysis prompts
        security_prompts = fetch_security_analysis_prompts()
        
        # Create a dropdown for selecting the analysis type
        analysis_types = [prompt['PromptID'] for prompt in security_prompts]
        selected_analysis = st.selectbox("Select Security Analysis Type", analysis_types)
        
        if st.button("Perform Security Analysis"):
            # Find the selected prompt
            selected_prompt = next((prompt for prompt in security_prompts if prompt['PromptID'] == selected_analysis), None)
            
            if selected_prompt:
                # Prepare the prompt for Bedrock
                transcript = selected_row['Analysis']
                full_prompt = f"{selected_prompt['PromptText']}\n\n<transcript>{transcript}</transcript>"
                
                # Invoke Bedrock
                with st.spinner("Performing security analysis..."):
                    analysis_result = invoke_bedrock(full_prompt)
                
                if analysis_result:
                    st.subheader("Security Analysis Result")
                    st.text_area("Analysis", analysis_result, height=300)
                else:
                    st.error("Failed to perform security analysis. Please try again.")
            else:
                st.error("Selected analysis type not found.")

    # Display video player if VideoS3URI is available
    if selected_transcript and 'VideoS3URI' in selected_row and selected_row['VideoS3URI'] and type(selected_row['VideoS3URI']) is str:
        st.subheader("Video Player")
        
        # Get S3 bucket and key from the VideoS3URI
        bucket, key = get_s3_details_from_uri(selected_row['VideoS3URI'])
        
        # Generate presigned URL
        presigned_url = create_presigned_url(bucket, key)
        
        if presigned_url:
            # Use st.empty() to create a placeholder for the video
            video_placeholder = st.empty()
            
            # Display the video using the presigned URL
            video_placeholder.video(presigned_url)
            
            # Add a button to refresh the video if it's not playing
            if st.button("Video not playing? Click to refresh"):
                # Generate a new presigned URL
                new_presigned_url = create_presigned_url(bucket, key)
                if new_presigned_url:
                    # Update the video with the new URL
                    video_placeholder.video(new_presigned_url)
                else:
                    st.error("Failed to generate a new video URL. Please try again later.")
        else:
            st.error("Failed to generate video URL. Please try again later.")
    else:
        st.info("No video available for this transcript.")

    

if __name__ == "__main__":
    show()
