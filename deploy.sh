#!/bin/bash
set -euo pipefail

FUNCTION_NAME="erith-launch-windows"
ROLE_NAME="erith-launch-windows-role"
REGION="eu-west-2"

# --- Require --profile ---
PROFILE=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --profile) PROFILE="$2"; shift 2 ;;
        *) echo "Usage: ./deploy.sh --profile <aws-profile>"; exit 1 ;;
    esac
done

if [ -z "$PROFILE" ]; then
    echo "Error: --profile is required"
    echo "Usage: ./deploy.sh --profile <aws-profile>"
    exit 1
fi

# --- Block Seatfrog profiles ---
if [[ "$PROFILE" == "app-uat" || "$PROFILE" == "app-prod" ]]; then
    echo "Error: Cannot deploy to Seatfrog accounts ($PROFILE)."
    echo "Use your personal AWS profile."
    exit 1
fi

AWS="aws --profile $PROFILE --region $REGION"

echo "Deploying to profile: $PROFILE, region: $REGION"

# --- SSM Parameters ---
echo "Checking SSM parameters..."
if ! $AWS ssm get-parameter --name /erith-launch/admiralty-api-key-primary --with-decryption &>/dev/null; then
    read -p "Enter primary Admiralty API key: " PRIMARY_KEY
    $AWS ssm put-parameter \
        --name /erith-launch/admiralty-api-key-primary \
        --value "$PRIMARY_KEY" \
        --type SecureString
    echo "Primary key stored."
fi
if ! $AWS ssm get-parameter --name /erith-launch/admiralty-api-key-secondary --with-decryption &>/dev/null; then
    read -p "Enter secondary Admiralty API key: " SECONDARY_KEY
    $AWS ssm put-parameter \
        --name /erith-launch/admiralty-api-key-secondary \
        --value "$SECONDARY_KEY" \
        --type SecureString
    echo "Secondary key stored."
fi

# --- IAM Role ---
echo "Setting up IAM role..."
ACCOUNT_ID=$($AWS sts get-caller-identity --query Account --output text)

TRUST_POLICY='{
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
    }]
}'

if ! $AWS iam get-role --role-name $ROLE_NAME &>/dev/null; then
    $AWS iam create-role \
        --role-name $ROLE_NAME \
        --assume-role-policy-document "$TRUST_POLICY"
    $AWS iam attach-role-policy \
        --role-name $ROLE_NAME \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    SSM_POLICY="{
        \"Version\": \"2012-10-17\",
        \"Statement\": [{
            \"Effect\": \"Allow\",
            \"Action\": \"ssm:GetParameter\",
            \"Resource\": \"arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/erith-launch/*\"
        }]
    }"
    $AWS iam put-role-policy \
        --role-name $ROLE_NAME \
        --policy-name erith-launch-ssm-read \
        --policy-document "$SSM_POLICY"

    echo "Waiting for role propagation..."
    sleep 10
fi

ROLE_ARN=$($AWS iam get-role --role-name $ROLE_NAME --query Role.Arn --output text)

# --- Package Lambda ---
echo "Packaging Lambda..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR=$(mktemp -d)
python3 -m pip install -r "$SCRIPT_DIR/lambda/requirements.txt" -t "$BUILD_DIR" --quiet
cp "$SCRIPT_DIR/lambda/lambda_function.py" "$BUILD_DIR/"
cp "$SCRIPT_DIR/lambda/tide_logic.py" "$BUILD_DIR/"
(cd "$BUILD_DIR" && zip -r9 "$SCRIPT_DIR/lambda.zip" . -x "*.pyc" "__pycache__/*" > /dev/null)
rm -rf "$BUILD_DIR"

# --- Create/Update Lambda ---
echo "Deploying Lambda..."
if $AWS lambda get-function --function-name $FUNCTION_NAME &>/dev/null; then
    $AWS lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file "fileb://$SCRIPT_DIR/lambda.zip" > /dev/null
else
    $AWS lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime python3.12 \
        --handler lambda_function.handler \
        --role "$ROLE_ARN" \
        --zip-file "fileb://$SCRIPT_DIR/lambda.zip" \
        --timeout 30 \
        --memory-size 128 > /dev/null
fi

# --- Function URL ---
echo "Setting up Function URL..."
if ! $AWS lambda get-function-url-config --function-name $FUNCTION_NAME &>/dev/null; then
    $AWS lambda add-permission \
        --function-name $FUNCTION_NAME \
        --statement-id function-url-public \
        --action lambda:InvokeFunctionUrl \
        --principal "*" \
        --function-url-auth-type NONE > /dev/null

    $AWS lambda add-permission \
        --function-name $FUNCTION_NAME \
        --statement-id FunctionURLAllowInvokeAction \
        --action lambda:InvokeFunction \
        --principal "*" \
        --function-url-auth-type NONE > /dev/null

    $AWS lambda add-permission \
        --function-name $FUNCTION_NAME \
        --statement-id FunctionURLAllowPublicAccess \
        --action lambda:InvokeFunctionUrl \
        --principal "*" \
        --function-url-auth-type NONE > /dev/null

    FUNCTION_URL=$($AWS lambda create-function-url-config \
        --function-name $FUNCTION_NAME \
        --auth-type NONE \
        --cors '{"AllowOrigins":["*"],"AllowMethods":["GET"]}' \
        --query FunctionUrl --output text)
else
    FUNCTION_URL=$($AWS lambda get-function-url-config \
        --function-name $FUNCTION_NAME \
        --query FunctionUrl --output text)
fi

rm -f "$SCRIPT_DIR/lambda.zip"

echo ""
echo "Done! Lambda Function URL:"
echo "  $FUNCTION_URL"
echo ""
echo "Update LAMBDA_URL in frontend/index.html with this URL."
