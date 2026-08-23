from types import SimpleNamespace
from tools.categories import category_map

from utils.dynamoDb import DynamoDBHelper
from strands import Agent, tool
from datetime import datetime
from enum import Enum
import uuid
from datetime import datetime;
from boto3.dynamodb.conditions import Key
from decimal import Decimal
from dateutil import parser
from decimal import Decimal
from botocore.exceptions import ClientError
import os
from logger_config import app, log
from google import genai
from google.genai import types

   
db = DynamoDBHelper(table_name=os.getenv("DYNAMO_DB_TABLE"), region_name=os.getenv("AWS_DEFAULT_REGION"))

def convert_to_iso(date_str: str, time_str: str) -> str:
    """Convert a date and time string into a standard ISO 8601 format.

    Parses the combined date and time strings (prioritizing day-first ordering) 
    using `dateutil.parser` and returns the resulting datetime object as an ISO 8601 
    formatted string (e.g., "YYYY-MM-DDTHH:MM:SS").

    Args:
        date_str: The date string to parse.
        time_str: The time string to parse.

    Returns:
        str: The ISO 8601 formatted datetime string.
    """
    combined_str = f"{date_str} {time_str}"
    dt_obj = parser.parse(combined_str, dayfirst=True)
    return dt_obj.isoformat()

@tool
def get_subcategory_from_category(category):
  """Retrieves the subcategory corresponding to the specified category.

  Args:
      category (str): The primary category name to look up.

  Returns:
      str/list: The corresponding subcategory or list of subcategories from the
      category map.
  """
  return category_map[category]

def update_monthly_summary(
    db,
    user_id: str,
    month: int,
    year: int,
    category,
    spend_amount=0,
    income_amount=0,
    idempotency_key: str = None,
):
    """
    db: injected DynamoDBHelper instance (pass _db, or a test double)
    month/year: ints, so callers can't pass inconsistent formatting
    idempotency_key: unique id for the transaction being applied
                      (e.g. transaction_id) - required to prevent double-counting
    category: if provided and spend_amount != 0, also increments a
              per-category running total under categorySpend[category]
    """
    # Defensive cast: protects every caller, even ones that pass strings
    # (e.g. from date.split("-")[n]) without casting themselves.
    try:
        month = int(month)
        year = int(year)
    except (TypeError, ValueError) as e:
        raise ValueError(f"month/year must be int-convertible, got month={month!r}, year={year!r}") from e

    if not (1 <= month <= 12):
        raise ValueError(f"Invalid month: {month}")

    # Zero-pad, year-first so sort keys order chronologically
    sk = f"SUMMARY#{year:04d}-{month:02d}"

    # Skip no-op writes
    if spend_amount == 0 and income_amount == 0:
        return None

    # Convert to Decimal - DynamoDB rejects native floats
    spend_val = Decimal(str(spend_amount))
    income_val = Decimal(str(income_amount))

    track_category = bool(category) and spend_val != 0

    def _build_kwargs(init_category_map: bool):
        update_expression = (
            "SET #spend = if_not_exists(#spend, :zero) + :spend_val, "
            "#income = if_not_exists(#income, :zero) + :income_val"
        )
        expr_names = {"#spend": "totalSpend", "#income": "totalIncome"}
        expr_values = {
            ":spend_val": spend_val,
            ":income_val": income_val,
            ":zero": Decimal(0),
        }

        if track_category:
            expr_names["#catSpend"] = "categorySpend"
            if init_category_map:
                # First-touch only: creates the map so the nested path below
                # becomes valid. Guarded by attribute_not_exists in the
                # condition so it never clobbers an existing map.
                update_expression += ", #catSpend = if_not_exists(#catSpend, :empty_map)"
                expr_values[":empty_map"] = {}
            else:
                expr_names["#cat"] = category
                update_expression += (
                    ", #catSpend.#cat = if_not_exists(#catSpend.#cat, :zero) + :spend_val"
                )

        # Idempotency: track applied transaction ids on the item itself,
        # and fail the write if this one's already been applied.
        condition_expression = None
        if idempotency_key and not init_category_map:
            update_expression += ", #applied = list_append(if_not_exists(#applied, :empty_list), :new_key)"
            expr_names["#applied"] = "appliedTxnIds"
            expr_values[":empty_list"] = []
            expr_values[":new_key"] = [idempotency_key]
            condition_expression = "attribute_not_exists(#applied) OR not contains(#applied, :idem_check)"
            expr_values[":idem_check"] = idempotency_key
        elif init_category_map:
            # Init-only call: don't touch totals/idempotency, just make
            # sure we don't race-clobber a map another writer just created.
            condition_expression = "attribute_not_exists(#catSpend)"

        kwargs = dict(
            key={"PK": user_id, "SK": sk},
            update_expression=update_expression,
            expression_attribute_names=expr_names,
            expression_attribute_values=expr_values,
        )
        if condition_expression:
            kwargs["condition_expression"] = condition_expression
        return kwargs

    try:
        return db.update_item(**_build_kwargs(init_category_map=False))

    except ClientError as e:
        code = e.response["Error"]["Code"]

        if code == "ConditionalCheckFailedException":
            return None

        if track_category and code == "ValidationException" and "document path" in str(e):
            # categorySpend map didn't exist yet - initialize it, then
            # retry the real update. If another writer beat us to the
            # init (ConditionalCheckFailed on attribute_not_exists),
            # that's fine, just proceed to retry.
            try:
                db.update_item(**_build_kwargs(init_category_map=True))
            except ClientError as init_err:
                if init_err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise

            try:
                return db.update_item(**_build_kwargs(init_category_map=False))
            except ClientError as retry_err:
                if retry_err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return None
                raise

        raise
@tool
def put_expense_to_ddb(user_id: str,transactionId:str,category: str, sub_category: str, amount: int, date: str, spendType: str, description:str ,time: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")):
    """
    Store an expense record into the local DynamoDB database. 
    Use this tool whenever the user wants to log, record, or save an expense, purchase, or spending transaction.

    Parameters:
    - category (str): The main category or entity for the partition key (e.g., USER, PRATIK).
    - transactionId(str): Unique TransactionId for tracking the expense record and maintaining idempotency.
    - sub_category (str): The sub-category or specific identifier for the sort key (e.g., KITCHEN, GROCERIES).
    - amount (int): The numeric monetary amount paid for the expense.
    - date (str): The date when the expense occurred (preferably in DD-MM_YYYY format).
    - spendType (str): The category or type of spending it can be either CASH or ONLINE.
    - description(str): Desciprion on what the expense was done on. 
    - time (str, optional): The specific time the expense occurred. Defaults to an empty string if not provided.

    Returns:
    - str: "Data Saved in DDB" if successful, or "Unable to Save Data" if it fails.
    """
    try:
        

        try:
            iso_timestamp = convert_to_iso(date, time)
        except ValueError as e:
            return f"Failed to parse date/time for expense record: date={date}, time={time}, error={e}"


        to_embed_string = f"Category: {category}, SubCategory: {sub_category}, Description: {description}"

        descriptionEmbedding = generate_embedding(to_embed_string)
        if (descriptionEmbedding):
            log.info(f"unable to generate embedding for description")

        item = {
            "PK": user_id,
            "SK": f"TXN#{iso_timestamp}",
            "category": category,
            "transactionType": "EXPENSE",
            "sub_category": sub_category,
            "amount": amount,
            "date": date,
            "spendType": spendType,
            "timeStamp": iso_timestamp,
            "transactionId": transactionId,
            "description": description,
            "descriptionEmbedding": descriptionEmbedding
        }

        create_expense_record_response = db.put_item(item)
        log.info(f"Expense Record Create= {create_expense_record_response}")
        year, month = date.split("-")[0], date.split("-")[1]

        update_summary_record_response = update_monthly_summary(
            db,
            user_id,
            month,
            year,
            category,
            spend_amount=amount,
            idempotency_key=transactionId
        )
        log.info(f"Update Expense Record Create= {create_expense_record_response}")

        if create_expense_record_response is True and update_summary_record_response:
            return "Data Saved in DDB"
        else:
            return f"Partial failure saving expense: create={create_expense_record_response}, summary_update={update_summary_record_response}, transactionId={transactionId}, userId={user_id}"

    except ClientError as e:
        return f"DynamoDB ClientError while saving expense (transactionId={transactionId}): {e.response['Error']['Message']}"
    except (ValueError, IndexError) as e:
        return f"Failed to parse date '{date}' for expense record (transactionId={transactionId}): {e}"
    except Exception as e:
        return f"Unexpected error saving expense (transactionId={transactionId}): {e}"
@tool
def put_income_to_ddb(user_id: str,transactionId:str,category: str, sub_category: str, amount: int, date: str, incomeType: str, description: str,time: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")):
    """
    Store an income record into the local DynamoDB database. 
    Use this tool whenever the user wants to log, record, or save an expense, purchase, or spending transaction.

    Parameters:
    - category (str): The main category or entity for the partition key (e.g., USER, PRATIK).
    - transactionId(str): Unique TransactionId for tracking the expense record and maintaining idempotency.
    - sub_category (str): The sub-category or specific identifier for the sort key (e.g., KITCHEN, GROCERIES).
    - amount (int): The numeric monetary amount paid for the expense.
    - date (str): The date when the expense occurred (preferably in DD-MM_YYYY format).
    - incomeType (str): The category or type of income it can be either CASH or ONLINE.
    - description: Desciption on what the Income was done
    - time (str, optional): The specific time the expense occurred. Defaults to an empty string if not provided.

    Returns:
    - str: "Data Saved in DDB" if successful, or "Unable to Save Data" if it fails.
    """
    transactionId = str(uuid.uuid7())

    to_embed_string = f"Category: {category}, SubCategory: {sub_category}, Description: {description}"
    
    descriptionEmbedding = generate_embedding(to_embed_string)
    if (type(descriptionEmbedding) is not list):
        log.info(f"unable to generate embedding for description")

    item = {
        "PK": user_id,
        "SK": f"TXN#{convert_to_iso(date,time)}",
        "category": category,
        "sub_category": sub_category,
        "transactionType": "INCOME",
        "amount": amount,
        "date": date,
        "incomeType": incomeType,
        "timeStamp": convert_to_iso(date,time),
        "transactionId": transactionId,
        "description": description,
        "descriptionEmbedding": descriptionEmbedding
    }
    create_expense_record_response = db.put_item(item)
    print("Expense Record Create=",create_expense_record_response)
    year, month = date.split("-")[0], date.split("-")[1]

    update_summary_record_response = update_monthly_summary(db,user_id,month,year,income_amount=amount,idempotency_key=transactionId)
    print("Update Summary record",update_summary_record_response)

    if (create_expense_record_response == True and update_summary_record_response):
        return "Data Saved in DDB"
    else:
        return "Unable to Save Data"
@tool
def fetch_records(
    user_id: str,
    start_date: str,   # "YYYY-MM-DD"
    end_date: str,     # "YYYY-MM-DD"
) -> dict:
    """Fetch and aggregate transaction records for a user from DynamoDB within a date range.

    Queries the DynamoDB table using a sort key range between the start of 
    `start_date` (00:00:00) and the end of `end_date` (23:59:59). Calculates 
    totals for income, expenses, and net amount using `decimal.Decimal`.

    Args:
        user_id: The partition key (PK) representing the user identifier.
        start_date: The lower bound date for the sort key (SK) range in "YYYY-MM-DD" format.
        end_date: The upper bound date for the sort key (SK) range in "YYYY-MM-DD" format.

    Returns:
        A dictionary containing summary totals (`total_income`, `total_expense`, 
        `net_amount`, `transaction_count`) and the raw list of DynamoDB items.
    """
    start_sk = f"TXN#{start_date}T00:00:00"
    end_sk = f"TXN#{end_date}T23:59:59"

    key_condition = (
        Key("PK").eq(user_id) &
        Key("SK").between(start_sk, end_sk)
    )

    items = db.query_items(key_condition_expression=key_condition)

    total_income = Decimal("0")
    total_expense = Decimal("0")
    print("Items:", items)

    for item in items:
        amount = item.get("amount", Decimal("0"))
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))

        txn_type = item.get("transactionType")
        print(txn_type)
        if txn_type == "INCOME":
            total_income += amount
        elif txn_type == "EXPENSE":
            print("amount")
            total_expense += amount

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_income": total_income,
        "total_expense": total_expense,
        "net_amount": total_income - total_expense,
        "transaction_count": len(items),
        "transactions": items,
    }
@tool
def fetch_monthly_summary_records(
    user_id: str,
    month: str,
    year: str
) -> dict:
    """Fetch and aggregate the monthly summary record for a user from DynamoDB.

    Queries the DynamoDB table using an exact sort key (SK) match for the specified 
    month and year in the format ``SUMMARY#YYYY-MM``. Aggregates the totals for 
    income and expenses from the returned summary items.

    Args:
        user_id: The partition key (PK) representing the unique user identifier.
        month: The target month for the summary in "MM" format (e.g., "01" for January).
        year: The target year for the summary in "YYYY" format (e.g., "2026").

    Returns:
        A dictionary containing the calculated summary totals and items:
            - **total_income** (Decimal): Total income aggregated for the month.
            - **total_expense** (Decimal): Total spend/expenses aggregated for the month.
            - **net_amount** (Decimal): The net balance (`total_income` - `total_expense`).
            - **transaction_count** (int): The number of summary items retrieved (typically 0 or 1).
            - **transactions** (list): The raw summary items returned from DynamoDB.
    """

    key_condition = (
        Key("PK").eq(user_id) &
        Key("SK").eq(f"SUMMARY#{year}-{month}")
    )

    items = db.query_items(key_condition_expression=key_condition)

    total_income = Decimal("0")
    total_expense = Decimal("0")
    print("Items:", items)

    for item in items:
        print("item",item)
        total_income += item["totalIncome"]
        total_expense += item["totalSpend"]

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "net_amount": total_income - total_expense,
        "transaction_count": len(items),
        "transactions": items,
    }

@tool
def fetch_by_vector_search(
    user_id: str,
    description: str
):
    """
    Vector-searches a user's transactions by natural-language description (e.g. "spending
    on eating out"). Use for descriptive/category spend queries, not exact filters.

    Args:
        user_id: User's chat_id / partition key.
        description: Natural-language expense category to search for.

    Returns:
        List of matching transaction records (with similarity scores) for the caller to
        aggregate, or an error string if the search fails.
    """
    embedding_vector = generate_embedding(description)

    search_item = SimpleNamespace(
        user_id=user_id,
        indexName="description",
        vectors=embedding_vector,
        limit=10
    )
    import boto3, botocore, sys
    ops = boto3.client('dynamodb', region_name='us-east-1').meta.service_model.operation_names
    return db.vector_search(search_item)

def generate_embedding(text: str):

    client = genai.Client()
    result = client.models.embed_content(
        model="gemini-embedding-2",
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=1024)
    )
    values = result.embeddings[0].values
    return [Decimal(str(v)) for v in values]

if __name__=="__main__":

    response = fetch_monthly_summary_records("7758425923","08","2026")
    print (response)
    




