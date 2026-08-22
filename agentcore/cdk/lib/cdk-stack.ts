import {
  AgentCoreApplication,
  type AgentCoreProjectSpec,
} from '@aws/agentcore-cdk';
import { CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as ssm from 'aws-cdk-lib/aws-ssm';

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /**
   * Credential provider ARNs from deployed state, keyed by credential name.
   */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
  /**
   * Name of the agent (key in AgentCoreProjectSpec.agents / this.application.environments)
   * whose runtime ARN should be exposed as `primaryAgentRuntimeArn` for downstream stacks
   * (e.g. WebhookStack). If omitted, the first environment found is used.
   */
  primaryAgentName?: string;
  /**
   * DynamoDB table name/ARN the agent runtime needs access to.
   */
  dynamoTableName: string;
}

/**
 * CDK Stack that deploys AgentCore infrastructure.
 *
 * Thin wrapper around the L3 AgentCoreApplication construct: wires the
 * DynamoDB table + Gemini API key into the agent runtime and exposes the
 * runtime ARN for the WebhookStack to invoke.
 */
export class AgentCoreStack extends Stack {
  /** The AgentCore application containing all agent environments */
  public readonly application: AgentCoreApplication;

  /** ARN of the AgentCore runtime consumed by downstream stacks (e.g. WebhookStack) */
  public readonly primaryAgentRuntimeArn: string;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const geminiApiKey = ssm.StringParameter.valueForStringParameter(
      this, '/financemanager2/gemini/api-key'
    );

    const { spec, credentials, primaryAgentName, dynamoTableName } = props;

    const appProps: Record<string, unknown> = { spec };
    if (credentials) {
      appProps.credentials = credentials;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    this.application = new AgentCoreApplication(this, 'Application', appProps as any);

    // Resolve the runtime ARN for the agent the webhook Lambda needs to invoke.
    const targetEnv = primaryAgentName
      ? this.application.environments.get(primaryAgentName)
      : this.application.environments.values().next().value;

    if (!targetEnv) {
      throw new Error(
        primaryAgentName
          ? `Expected agent environment "${primaryAgentName}" not found in application spec`
          : 'No agent environments found in AgentCoreApplication'
      );
    }

    targetEnv.runtime.addEnvironmentVariable('GEMINI_API_KEY', geminiApiKey);
    targetEnv.runtime.addEnvironmentVariable('AWS_DEFAULT_REGION', this.region);
    targetEnv.runtime.addEnvironmentVariable('DYNAMO_DB_TABLE', dynamoTableName);

    // DynamoDB access via the runtime's own execution role — no static keys needed.
    targetEnv.runtime.role.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: [
          'dynamodb:PutItem',
          'dynamodb:GetItem',
          'dynamodb:UpdateItem',
          'dynamodb:Query',
          'dynamodb:DeleteItem',
        ],
        resources: ["*"],
      })
    );

    this.primaryAgentRuntimeArn = targetEnv.runtime.runtimeArn;

    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });

    new CfnOutput(this, 'PrimaryAgentRuntimeArn', {
      description: 'ARN of the AgentCore runtime the Telegram webhook Lambda invokes',
      value: this.primaryAgentRuntimeArn,
      exportName: 'FinanceManager2-AgentRuntimeArn',
    });
  }
}