import logging
from typing import Any, Dict, List, Optional
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class DynamoDBHelper:
    """A reusable helper class for standard DynamoDB operations using boto3."""

    def __init__(self, table_name: str, region_name: Optional[str] = None):
        """
        Initializes the DynamoDB helper.

        Args:
            table_name: The name of the DynamoDB table.
            region_name: Optional AWS region (e.g., 'us-east-1'). If not provided, 
                         boto3 will use the default configuration/environment.
        """
        self.table_name = table_name
        self.dynamodb = boto3.resource(
    'dynamodb',
    region_name='us-east-1',
)
        self.table = self.dynamodb.Table(table_name)
        self.client = boto3.client('dynamodb', region_name='us-east-1')

    def put_item(self, item: Dict[str, Any]) -> bool:
        """
        Inserts or replaces an item in the table.

        Args:
            item: A dictionary representing the item to store.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self.table.put_item(Item=item)
            return True
        except ClientError as e:
            logger.error(f"Error putting item into {self.table_name}: {e.response['Error']['Message']}")
            return False

    def get_item(self, key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single item by its primary key.

        Args:
            key: A dictionary representing the primary key (e.g., {'id': '123'}).

        Returns:
            The item dictionary if found, or None.
        """
        try:
            response = self.table.get_item(Key=key)
            return response.get('Item')
        except ClientError as e:
            logger.error(f"Error getting item from {self.table_name}: {e.response['Error']['Message']}")
            return None

    def update_item(
        self,
        key: Dict[str, Any],
        update_expression: str,
        expression_attribute_values: Dict[str, Any],
        expression_attribute_names: Optional[Dict[str, str]] = None,
        condition_expression: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Updates an existing item's attributes.

        Args:
            key: Primary key of the item to update.
            update_expression: The update expression (e.g., 'SET #status = :val').
            expression_attribute_values: Values mapped to the update expression.
            expression_attribute_names: Optional alias map for reserved words.
            condition_expression: Optional condition; raises ConditionalCheckFailedException
                                   (as a ClientError) if not met, so callers can distinguish
                                   a blocked write from other failures.

        Returns:
            The attributes of the updated item (if configured) or None.

        Raises:
            ClientError: for any DynamoDB error, including conditional check failures.
                         Callers should catch this and inspect e.response['Error']['Code']
                         if they need to handle ConditionalCheckFailedException specially.
        """
        kwargs: Dict[str, Any] = {
            'Key': key,
            'UpdateExpression': update_expression,
            'ExpressionAttributeValues': expression_attribute_values,
            'ReturnValues': 'ALL_NEW'
        }
        if expression_attribute_names:
            kwargs['ExpressionAttributeNames'] = expression_attribute_names
        if condition_expression:
            kwargs['ConditionExpression'] = condition_expression

        try:
            response = self.table.update_item(**kwargs)
            return response.get('Attributes')
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # Expected/benign - let caller decide how to handle (e.g. idempotent no-op)
                raise
            logger.error(f"Error updating item in {self.table_name}: {e.response['Error']['Message']}")
            raise

    def delete_item(self, key: Dict[str, Any]) -> bool:
        """
        Deletes an item by its primary key.

        Args:
            key: The primary key of the item to delete.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self.table.delete_item(Key=key)
            return True
        except ClientError as e:
            logger.error(f"Error deleting item from {self.table_name}: {e.response['Error']['Message']}")
            return False

    def query_items(
        self,
        key_condition_expression: Any,
        expression_attribute_values: Optional[Dict[str, Any]] = None,
        expression_attribute_names: Optional[Dict[str, str]] = None,
        index_name: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Queries items from the table using a key condition expression.

        Args:
            key_condition_expression: boto3 condition expression (e.g., Key('id').eq('123')).
            expression_attribute_values: Values for the expression if using raw strings.
            expression_attribute_names: Alias map for reserved words.
            index_name: Optional Global or Local Secondary Index name.
            limit: Optional maximum number of items to evaluate.

        Returns:
            A list of matching items.
        """
        try:
            query_kwargs: Dict[str, Any] = {
                'KeyConditionExpression': key_condition_expression
            }
            if expression_attribute_values:
                query_kwargs['ExpressionAttributeValues'] = expression_attribute_values
            if expression_attribute_names:
                query_kwargs['ExpressionAttributeNames'] = expression_attribute_names
            if index_name:
                query_kwargs['IndexName'] = index_name
            if limit:
                query_kwargs['Limit'] = limit

            response = self.table.query(**query_kwargs)
            items = response.get('Items', [])

            # Handle pagination if 'LastEvaluatedKey' exists and no strict limit was applied
            while 'LastEvaluatedKey' in response and not limit:
                query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                response = self.table.query(**query_kwargs)
                items.extend(response.get('Items', []))

            return items
        except ClientError as e:
            logger.error(f"Error querying {self.table_name}: {e.response['Error']['Message']}")
            return []

    def scan_table(self) -> List[Dict[str, Any]]:
        """
        Scans the entire table (Use sparingly on large tables).

        Returns:
            A list of all items in the table.
        """
        try:
            response = self.table.scan()
            items = response.get('Items', [])

            while 'LastEvaluatedKey' in response:
                response = self.table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                items.extend(response.get('Items', []))

            return items
        except ClientError as e:
            logger.error(f"Error scanning {self.table_name}: {e.response['Error']['Message']}")
            return []

    def vector_search(self, item: Dict[str, Any]):
        '''
        Performs Vector search on the records stored in dynamodb.
        '''
        try:
            response = self.client.search_vectors(
                TableName=self.table_name,
                IndexName=item.indexName,
                SearchVector=[{'N': str(v)} for v in item.vectors],
                TopK=item.limit,
                SearchConditionExpression='#pk = :pk_val',
                ExpressionAttributeNames={
                    '#pk': 'PK'
                },
                ExpressionAttributeValues={
                    ':pk_val': {'S': item.user_id}
                }
            )
            results = response.get('SearchResults', [])
            for record in results:
                result_item = record.get('Item', {})
                similarity_score = record.get('SimilarityScore')
                logger.info(f"Item ID: {result_item.get('ProductId', {}).get('S')}")
                logger.info(f"Similarity Score: {similarity_score}")

            return results
        except ClientError as e:
            logger.error(f"Vector search error in {self.table_name}: {e.response['Error']['Message']}")
            return []
