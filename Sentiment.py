import pandas as pd
import openai
import time
from tqdm import tqdm
from collections import OrderedDict
import concurrent.futures
import logging
import json
import re

openai.api_key = "YOUR_API_KEY"

DOMAIN_NAME = "Add Domain name"
MODEL = "gpt-4o-mini"

BATCH_SIZE = 10

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_completion(prompt):
    try:
        response = openai.ChatCompletion.create(
            model=MODEL,
            temperature=0,
            top_p=0,
            seed = 1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system",
                 "content": f"You're an expert in sentiment analysis, tasked with assessing the sentiment of text data."},
                {"role": "user",
                 "content": prompt}
            ]
        )
        return response.choices[0].message["content"]
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        time.sleep(2)
        return None


def create_prompts(data):
    prompts = {}
    for _, row in data.iterrows():
        theme = row["Raw Theme"]
        sentence = row["Review"]
        prompt = (
                f"Domain: {DOMAIN_NAME}\n"
                f"Theme: {theme}\n"
                f"Sentence: \"{sentence}\"\n\n"
                "Objective:\n"
                f"Identify the central idea of the sentence to ensure the context is properly understood.\n"
                f"Focus on understanding how the {DOMAIN_NAME} domain and {theme} is conveyed in the sentence.\n"
                f"Rate the sentiment intensity of the sentence on a scale from -5 to +5, where -5 is extremely negative, 0 is neutral, and +5 is extremely positive.\n"
                f"Classify the sentiment of the sentence into one of five categories: Negative (-5, -4), Mildly Negative (-3, -2), Neutral (-1, 0, 1), Mildly Positive (2, 3), Positive (4, 5).\n"
                "STRICT INSTRUCTION: Determine the overall sentiment and classify it into one of three categories: Negative, Neutral, and Positive.\n"
                "If the sentence does not contain any strong emotional expressions or subjective language and no sentiment is conveyed, return Neutral.\n\n"
                "Response Format JSON:\n"
                "{\n"
                "  \"Overall Sentiment\": {\n"
                "    \"Overall Sentiment Intensity\": \"[Overall Sentiment Intensity]\",\n"
                "    \"Overall Sentiment Tag\": \"[Overall Sentiment Tag]\"\n"
                "  }\n"
                "}"
        )

        prompts[(sentence,)] = {"prompt": prompt}
    return prompts

def process_prompt(key, prompt, retry_delay=2):
    while True:
        response = generate_completion(prompt)
        if response:
            logger.info(f"Raw response for {key}: {response}")
            return key, response
        else:
            logger.warning(f"No response for {key}. Retrying...")
            time.sleep(retry_delay)

def split_into_batches(data, batch_size):
    num_batches = len(data) // batch_size + (1 if len(data) % batch_size != 0 else 0)
    return [data.iloc[i * batch_size:(i + 1) * batch_size] for i in range(num_batches)]

def expand_json(row):
    response = row.get('FT_Response', '')
    if not response:
        logger.warning(f"Empty response for row: {row}")
        row['Overall Sentiment Intensity'] = None
        row['Overall Sentiment Tag'] = 'Neutral'
        return row

    try:
        json_data = json.loads(response)
        overall_sentiment = json_data['Overall Sentiment']
        intensity = overall_sentiment['Overall Sentiment Intensity']
#         print(intensity)
        if intensity == 'Neutral':
            intensity = 0

        if -1 <= int(intensity)  <= 1:
            row['Overall Sentiment Intensity'] = 0
            row['Overall Sentiment Tag'] = 'Neutral'
        else:
            row['Overall Sentiment Intensity'] = intensity
            overall_tag = overall_sentiment['Overall Sentiment Tag']

            if overall_tag in ['Positive', 'Mildly Positive']:
                row['Overall Sentiment Tag'] = 'Positive'
            elif overall_tag in ['Negative', 'Mildly Negative']:
                row['Overall Sentiment Tag'] = 'Negative'
            else:
                row['Overall Sentiment Tag'] = 'Neutral'

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error for row: {row} - {e}")
        row['Overall Sentiment Intensity'] = None
        row['Overall Sentiment Tag'] = 'Neutral'

    return row

def data_normalization(row):

    score = row['Overall Sentiment Intensity']
    normalized_score = (2 * (int(score) - (-5))) / (5 - (-5)) - 1
    normalized_score = round(normalized_score, 2)

    row['Overall Sentiment Intensity'] = normalized_score

    return row

def main():
    input_data_path = "Sentiment.csv"
    input_data = pd.read_csv(input_data_path,nrows=2000)

    batches = split_into_batches(input_data, BATCH_SIZE)
    all_prompts = OrderedDict()

    start_time = time.time()

    for batch_num, batch in enumerate(tqdm(batches)):
        logger.info(f"Processing batch {batch_num + 1}/{len(batches)}")
        prompts = create_prompts(batch)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_key = {executor.submit(process_prompt, key, val['prompt']): key for key, val in prompts.items()}

            for future in tqdm(concurrent.futures.as_completed(future_to_key), total=len(prompts)):
                key = future_to_key[future]
                try:
                    key, response = future.result()
                    prompts[key]["response"] = response
                    logger.info(f"Key: {key}\nResponse: {response}\n")
                except Exception as exc:
                    logger.error(f'{key} generated an exception: {exc}')
                    prompts[key]["response"] = "Error"

        all_prompts.update(prompts)

    end_time = time.time()
    logger.info(f"Time taken: {end_time - start_time} seconds")

    for i in range(len(input_data)):
        sentence = input_data.loc[i, "Review"]
        key = (sentence,)
        input_data.loc[i, "Response"] = all_prompts.get(key, {}).get("response", "Result_NF")

    expanded_data = input_data.apply(expand_json, axis=1)
    normalized_data = expanded_data.apply(data_normalization, axis=1)

    output_file_path = "Output.csv"
    normalized_data.to_csv(output_file_path, index=False)
    logger.info(f"Data saved to {output_file_path}")

if __name__ == "__main__":
    main()