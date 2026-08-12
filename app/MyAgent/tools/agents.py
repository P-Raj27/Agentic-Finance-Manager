from strands import Agent, tool
from tools.dynamo_db_tools import put_expense_to_ddb,get_subcategory_from_category,category_map,put_income_to_ddb,fetch_records,fetch_monthly_summary_records
from model.load import load_model

intents = [
    "RECORD_EXPENSE",
    "RECORD_INCOME",
    "FETCH_RECORDS"
]


from google.genai import types

@tool
def call_intent_agent(query: str) -> str:
    """
    Analyzes the user query to determine the intent from the allowed list.
    Use this tool to figure out the user's underlying intent.

    Parameters:
    - query (str): The raw text query provided by the user.

    Returns:
    - str: The detected intent category.
    """
    print("call_intent_agent recieve:",query)
    intent_agent = Agent(
        model=load_model(),
        name="Intent Finder Agent",
        system_prompt=f"You are an intelligent AI sentiment analyzer, whose sole job is to analyze the user query and understand the intent from the list: {intents}",
        description="This Agents Job is to take the user query and analyze it and provide the intent so that the orchestrater agent can decide which function to call"
    )
    response = intent_agent(query)
    return str(response)

@tool
def call_records_agent(intent: str, query: str) -> str:
    """
    Acts as a record keeper to handle logging, storing,updating and fetching records based on the provided intent.
    Use this tool when you need to save or record user data.
     This tool to be called when the intent is RECORD_EXPENSE,RECORD_INCOME,FETCH_RECORDS

    Parameters:
    - intent (str): The intent category determined by the intent finder.
    - query (str): The raw text query or data to be processed and stored.

    Returns:
    - str: The execution result of the record-keeping operation.
    """
    print("call_records_agent recieve:",intent,query)
    records_agent = Agent(
        model=load_model(),
        name="Records Keeper Agent",
        system_prompt=f"""You are an intelligent Record keeper, whose main job is to call the appropriate tool based on the INTENT Provided to you. You First step is to identify proper catefory , sub category , amount etc.
        1. The Category must be one of {list(category_map.keys())}
        2. Spend Type Can only be one of Cash or Bank- You should ask user if its not specified, by default choose bank
        3. Use the userId , transactionId from the query to fill the parameters of the function you call wherever required""",
        tools=[put_expense_to_ddb,get_subcategory_from_category,put_income_to_ddb]
    )
    response = records_agent(f"Intent: {intent}, Query: {query}")
    return str(response)

@tool
def fetch_records_agent(intent: str, query:str) -> str:
    """
    Acts as a record fetcher to handle fetching of records as per the user query.
        Use this tool when you need to get the dynamo db records.
        This tool to be called when the intent is FETCH_RECORDS
    
        Parameters:
        - intent (str): The intent category determined by the intent finder.
        - query (str): The raw text query or data to be processed and stored.
    
        Returns:
        - str: The execution result of the record-keeping operation.
    """

    records_fetch_agent = Agent(
            model=load_model(),
            name="Records Fetcher Agent",
            system_prompt=f"""You are an intelligent Record Fetcher, whose main job is to call the appropriate tool based on the INTENT Provided to you.
            1. Your First Job is to get the proper dates for which the users are asking and call the tool with the userId and proper dates.
            2. Make sure to call the tool at any cost
            2. You have to analye the query and call the most optimal tool for the query to get the records and respond to user
            3. Params are user_id,start_date_end_date
            4. For month summary you have to fetch the Monthly Summary records""",
            tools=[fetch_records,fetch_monthly_summary_records]
        )
    response = records_fetch_agent(f"Intent: {intent}, Query: {query}")
    return str(response)


    