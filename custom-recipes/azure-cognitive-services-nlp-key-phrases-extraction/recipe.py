# -*- coding: utf-8 -*-
from typing import List, Dict, AnyStr

from retry import retry
from ratelimit import limits, RateLimitException

import dataiku
from dataiku.customrecipe import get_recipe_config, get_input_names_for_role, get_output_names_for_role

from plugin_io_utils import ErrorHandlingEnum, validate_column_input
from dku_io_utils import set_column_description
from azure_nlp_api_client import API_EXCEPTIONS, batch_api_response_parser, AzureNLPAPIWrapper
from api_parallelizer import api_parallelizer
from azure_nlp_api_formatting import KeyPhraseExtractionAPIFormatter


# ==============================================================================
# SETUP
# ==============================================================================

api_configuration_preset = get_recipe_config().get("api_configuration_preset")
api_quota_rate_limit = api_configuration_preset.get("api_quota_rate_limit")
api_quota_period = api_configuration_preset.get("api_quota_period")
parallel_workers = api_configuration_preset.get("parallel_workers")
batch_size = api_configuration_preset.get("batch_size")
text_column = get_recipe_config().get("text_column")
text_language = get_recipe_config().get("language")
language_column = get_recipe_config().get("language_column")
num_key_phrases = int(get_recipe_config().get("num_key_phrases"))
error_handling = ErrorHandlingEnum[get_recipe_config().get("error_handling")]

input_dataset_name = get_input_names_for_role("input_dataset")[0]
input_dataset = dataiku.Dataset(input_dataset_name)
input_schema = input_dataset.read_schema()
input_columns_names = [col["name"] for col in input_schema]

output_dataset_name = get_output_names_for_role("output_dataset")[0]
output_dataset = dataiku.Dataset(output_dataset_name)

validate_column_input(text_column, input_columns_names)
if text_language == "language_column":
    validate_column_input(language_column, input_columns_names)
input_df = input_dataset.get_dataframe()
api_wrapper = AzureNLPAPIWrapper(api_configuration_preset)
column_prefix = "keyphrase_api"

batch_kwargs = {
    "api_support_batch": True,
    "batch_size": batch_size,
    "batch_api_response_parser": batch_api_response_parser,
}


# ==============================================================================
# RUN
# ==============================================================================


@retry((RateLimitException, OSError), delay=api_quota_period, tries=5)
@limits(calls=api_quota_rate_limit, period=api_quota_period)
def call_api_key_phrase_extraction(
    batch: List[Dict], text_column: AnyStr, text_language: AnyStr, language_column: AnyStr = None,
) -> List[Dict]:
    document_list = {
        "documents": [
            {
                "id": str(index),
                "text": str(row.get(text_column, "")).strip(),
                "language": str(row.get(language_column, "")) if text_language == "language_column" else text_language,
            }
            for index, row in enumerate(batch)
        ]
    }
    responses = api_wrapper.extract_keyphrases(document_list)
    return responses


df = api_parallelizer(
    input_df=input_df,
    api_call_function=call_api_key_phrase_extraction,
    api_exceptions=API_EXCEPTIONS,
    column_prefix=column_prefix,
    text_column=text_column,
    text_language=text_language,
    language_column=language_column,
    parallel_workers=parallel_workers,
    error_handling=error_handling,
    **batch_kwargs
)

api_formatter = KeyPhraseExtractionAPIFormatter(
    input_df=input_df, num_key_phrases=num_key_phrases, column_prefix=column_prefix, error_handling=error_handling
)
output_df = api_formatter.format_df(df)

output_dataset.write_with_schema(output_df)
set_column_description(
    input_dataset=input_dataset,
    output_dataset=output_dataset,
    column_description_dict=api_formatter.column_description_dict,
)
