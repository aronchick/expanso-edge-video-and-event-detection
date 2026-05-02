#!/usr/bin/env bash
# Bootstrap the armyx-tech edge-ISR demo: AWS bucket + IAM user with scoped
# keys, AWS credentials distributed to Mac and Jetson, expanso-cli profile
# pointed at the armyx-tech cluster, and the S3 archive pipeline deployed.
#
# Idempotent. Safe to re-run. Re-running rotates nothing unless you pass
# --rotate-keys explicitly.
#
# Usage:
#   JETSON_HOST=nvidia@jetson.local ./scripts/bootstrap_armyx_tech.sh
#
# Optional env:
#   AWS_REGION                  default us-west-2
#   AWS_PROFILE                 admin profile used to provision (must allow IAM + S3 create)
#   ARMYX_EXPANSO_ENDPOINT      e.g. https://xxx.us2.cloud.expanso.io:9010
#   ARMYX_EXPANSO_API_KEY       Expanso Cloud API key for the armyx-tech cluster
#   ARMYX_BUCKET_OVERRIDE       use this exact bucket name instead of the default
#
# What it touches (all tagged Project=armyx-tech for easy teardown):
#   AWS:
#     - S3 bucket: armyx-tech-edge-events-<account-id>
#     - IAM user:  armyx-tech-edge
#     - IAM inline policy: armyx-tech-edge-policy (S3 RW scoped to the bucket)
#     - 1 set of access keys (created on first run)
#   Mac:
#     - ~/.aws/credentials profile [armyx-tech]
#     - ./.env (project) with ARMYX_* exports
#     - expanso-cli profile "armyx-tech" (created if endpoint+key supplied)
#   Jetson (over ssh):
#     - ~/.aws/credentials profile [armyx-tech]
#   Expanso Cloud:
#     - Pipeline job "armyx-tech-event-archive" deployed to the cluster
#
# Teardown: ./scripts/teardown_armyx_tech.sh

set -euo pipefail

PROJECT="armyx-tech"
EXPANSO_PROFILE="armyx-tech"
PIPELINE_NAME="armyx-tech-event-archive"
PIPELINE_FILE="jobs/${PIPELINE_NAME}.yaml"
REGION="${AWS_REGION:-us-west-2}"
ROTATE_KEYS=0
SKIP_JETSON=0
SKIP_DEPLOY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rotate-keys) ROTATE_KEYS=1 ;;
    --skip-jetson) SKIP_JETSON=1 ;;
    --skip-deploy) SKIP_DEPLOY=1 ;;
    --help|-h)
      sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

# ---------- prereqs ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "FAIL  required command not on PATH: $1"; exit 1; }; }
need aws
need expanso-cli
need jq
need ssh
need scp

if [[ -z "${JETSON_HOST:-}" && "$SKIP_JETSON" -eq 0 ]]; then
  echo "FAIL  JETSON_HOST is required (e.g. JETSON_HOST=nvidia@jetson.local)."
  echo "      Set it, or pass --skip-jetson to skip the Jetson credential push."
  exit 1
fi

# Sanity check the AWS profile actually has admin-ish reach.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || {
  echo "FAIL  aws sts get-caller-identity failed. Check AWS_PROFILE / SSO session."
  exit 1
}
echo "OK    AWS account ${ACCOUNT_ID}, region ${REGION}"

BUCKET="${ARMYX_BUCKET_OVERRIDE:-${PROJECT}-edge-events-${ACCOUNT_ID}}"
USER_NAME="${PROJECT}-edge"
POLICY_NAME="${PROJECT}-edge-policy"
SECRETS_DIR="$(pwd)/.${PROJECT}-secrets"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

# ---------- S3 bucket ----------
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "OK    bucket exists: s3://${BUCKET}"
else
  echo "INFO  creating bucket s3://${BUCKET}"
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null
  aws s3api put-bucket-versioning --bucket "$BUCKET" \
    --versioning-configuration Status=Enabled >/dev/null
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
  aws s3api put-bucket-tagging --bucket "$BUCKET" \
    --tagging "TagSet=[{Key=Project,Value=${PROJECT}}]" >/dev/null
  echo "OK    bucket created and hardened"
fi

# ---------- IAM user + scoped policy ----------
if aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  echo "OK    iam user exists: ${USER_NAME}"
else
  aws iam create-user --user-name "$USER_NAME" \
    --tags "Key=Project,Value=${PROJECT}" >/dev/null
  echo "OK    iam user created: ${USER_NAME}"
fi

POLICY_DOC=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BucketReadWrite",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::${BUCKET}/*"
    },
    {
      "Sid": "BucketList",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::${BUCKET}"
    }
  ]
}
JSON
)
aws iam put-user-policy \
  --user-name "$USER_NAME" \
  --policy-name "$POLICY_NAME" \
  --policy-document "$POLICY_DOC" >/dev/null
echo "OK    iam policy attached: ${POLICY_NAME} (S3 RW scoped to bucket)"

# ---------- access keys ----------
EXISTING_KEY_IDS=$(aws iam list-access-keys --user-name "$USER_NAME" \
  --query 'AccessKeyMetadata[].AccessKeyId' --output text)
KEYS_FILE="${SECRETS_DIR}/credentials"

if [[ "$ROTATE_KEYS" -eq 1 && -n "$EXISTING_KEY_IDS" ]]; then
  echo "INFO  rotating: deleting existing access keys"
  for kid in $EXISTING_KEY_IDS; do
    aws iam delete-access-key --user-name "$USER_NAME" --access-key-id "$kid" >/dev/null
  done
  EXISTING_KEY_IDS=""
fi

if [[ -z "$EXISTING_KEY_IDS" ]]; then
  echo "INFO  creating new access key pair"
  KEY_JSON=$(aws iam create-access-key --user-name "$USER_NAME")
  ACCESS_KEY_ID=$(echo "$KEY_JSON" | jq -r .AccessKey.AccessKeyId)
  SECRET_KEY=$(echo "$KEY_JSON" | jq -r .AccessKey.SecretAccessKey)
  cat > "$KEYS_FILE" <<EOF
[${PROJECT}]
aws_access_key_id = ${ACCESS_KEY_ID}
aws_secret_access_key = ${SECRET_KEY}
region = ${REGION}
EOF
  chmod 600 "$KEYS_FILE"
  echo "OK    access keys saved to ${KEYS_FILE}"
elif [[ ! -f "$KEYS_FILE" ]]; then
  echo "WARN  iam user already has keys but ${KEYS_FILE} is missing."
  echo "      The plaintext secret was only available at create time."
  echo "      Re-run with --rotate-keys to generate fresh ones, or recover"
  echo "      from your password manager."
  exit 1
else
  echo "OK    access keys already provisioned (use --rotate-keys to rotate)"
fi

# ---------- Mac: ~/.aws/credentials ----------
mkdir -p "$HOME/.aws"
chmod 700 "$HOME/.aws"
touch "$HOME/.aws/credentials"
chmod 600 "$HOME/.aws/credentials"

if grep -q "^\[${PROJECT}\]" "$HOME/.aws/credentials" 2>/dev/null; then
  echo "OK    ~/.aws/credentials already has [${PROJECT}] profile (left as-is)"
else
  printf '\n' >> "$HOME/.aws/credentials"
  cat "$KEYS_FILE" >> "$HOME/.aws/credentials"
  echo "OK    appended [${PROJECT}] profile to ~/.aws/credentials"
fi

# ---------- Jetson: scp ~/.aws/credentials ----------
if [[ "$SKIP_JETSON" -eq 0 ]]; then
  echo "INFO  pushing credentials to Jetson (${JETSON_HOST})"
  ssh -o BatchMode=yes -o ConnectTimeout=5 "$JETSON_HOST" \
    "mkdir -p ~/.aws && chmod 700 ~/.aws && touch ~/.aws/credentials && chmod 600 ~/.aws/credentials"

  # Idempotent merge: pull current remote file, append the [armyx-tech] block
  # only if absent, then push back. This avoids clobbering other profiles the
  # Jetson may already have for other demos.
  TMP_REMOTE=$(mktemp)
  trap 'rm -f "$TMP_REMOTE"' EXIT
  scp -q "${JETSON_HOST}:~/.aws/credentials" "$TMP_REMOTE" 2>/dev/null || : > "$TMP_REMOTE"
  if grep -q "^\[${PROJECT}\]" "$TMP_REMOTE" 2>/dev/null; then
    echo "OK    Jetson already has [${PROJECT}] profile (left as-is)"
  else
    printf '\n' >> "$TMP_REMOTE"
    cat "$KEYS_FILE" >> "$TMP_REMOTE"
    scp -q "$TMP_REMOTE" "${JETSON_HOST}:~/.aws/credentials"
    ssh "$JETSON_HOST" "chmod 600 ~/.aws/credentials"
    echo "OK    appended [${PROJECT}] profile to Jetson ~/.aws/credentials"
  fi
fi

# ---------- project .env ----------
ENV_FILE="$(pwd)/.env"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
update_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    # macOS sed needs an empty -i ''
    sed -i.bak "s|^${key}=.*|${key}=${val}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}
update_env "ARMYX_S3_BUCKET" "$BUCKET"
update_env "ARMYX_AWS_REGION" "$REGION"
update_env "ARMYX_AWS_PROFILE" "$PROJECT"
update_env "ARMYX_PROJECT" "$PROJECT"
update_env "ARMYX_PIPELINE_NAME" "$PIPELINE_NAME"
[[ -n "${JETSON_HOST:-}" ]] && update_env "ARMYX_JETSON_HOST" "$JETSON_HOST"
echo "OK    project .env updated"

# ---------- expanso-cli profile + pipeline deploy ----------
if [[ "$SKIP_DEPLOY" -eq 0 ]]; then
  # grep -w is column-agnostic; the table uses Unicode box separators and
  # active rows shift the profile name from col 2 to col 4 due to the "*" marker.
  if expanso-cli profile list 2>/dev/null | grep -qw "$EXPANSO_PROFILE"; then
    echo "OK    expanso-cli profile [${EXPANSO_PROFILE}] already configured"
  else
    if [[ -z "${ARMYX_EXPANSO_ENDPOINT:-}" || -z "${ARMYX_EXPANSO_API_KEY:-}" ]]; then
      echo "WARN  expanso-cli profile [${EXPANSO_PROFILE}] is not configured."
      echo "      Set ARMYX_EXPANSO_ENDPOINT and ARMYX_EXPANSO_API_KEY and re-run,"
      echo "      or run manually:"
      echo "        expanso-cli profile save ${EXPANSO_PROFILE} \\"
      echo "          --endpoint https://<your-cluster>.cloud.expanso.io:9010 \\"
      echo "          --api-key exp_ak_... --select"
      echo "      Skipping pipeline deploy."
      SKIP_DEPLOY=1
    else
      expanso-cli profile save "$EXPANSO_PROFILE" \
        --endpoint "$ARMYX_EXPANSO_ENDPOINT" \
        --api-key "$ARMYX_EXPANSO_API_KEY" \
        --select >/dev/null
      echo "OK    expanso-cli profile [${EXPANSO_PROFILE}] saved and selected"
    fi
  fi
fi

if [[ "$SKIP_DEPLOY" -eq 0 ]]; then
  # Make sure the right profile is active for the deploy.
  # `expanso-cli profile current` exits non-zero even on success (it prints the
  # profile then a printer-format error). Without `|| true` here, set -euo
  # pipefail propagates the failure through the command substitution and
  # silently kills the script before the deploy step.
  CURRENT=$(expanso-cli profile current 2>/dev/null | awk '/Current profile:/ {print $3}' || true)
  if [[ -n "$CURRENT" && "$CURRENT" != "$EXPANSO_PROFILE" ]]; then
    expanso-cli profile select "$EXPANSO_PROFILE" >/dev/null
    echo "OK    selected expanso-cli profile [${EXPANSO_PROFILE}]"
  fi

  if [[ ! -f "$PIPELINE_FILE" ]]; then
    echo "FAIL  pipeline yaml not found: ${PIPELINE_FILE}"
    exit 1
  fi
  echo "INFO  deploying pipeline ${PIPELINE_NAME}"
  expanso-cli job deploy "$PIPELINE_FILE" || {
    echo "WARN  pipeline deploy failed — check expanso-cli output above."
    echo "      Common causes: no edge nodes connected to the cluster yet, or"
    echo "      pipeline file references env vars not set on the edge node."
    exit 1
  }
  echo "OK    pipeline deployed: ${PIPELINE_NAME}"
fi

# ---------- summary ----------
cat <<EOF

=== ${PROJECT} bootstrap complete ===

  S3 bucket    : s3://${BUCKET}
  Region       : ${REGION}
  AWS profile  : ${PROJECT}  (in ~/.aws/credentials, on Mac and Jetson)
  Project env  : ${ENV_FILE}
  Secrets dir  : ${SECRETS_DIR}  (chmod 600, .gitignored)

Next steps:
  1. Verify Mac access:    aws --profile ${PROJECT} s3 ls s3://${BUCKET}/
  2. Verify Jetson access: ssh ${JETSON_HOST:-<jetson>} "aws s3 ls s3://${BUCKET}/"
  3. Confirm pipeline:     expanso-cli job describe ${PIPELINE_NAME}
  4. Tail live archive:    aws --profile ${PROJECT} s3 ls s3://${BUCKET}/events/ --recursive

Teardown (deletes everything tagged Project=${PROJECT}):
  ./scripts/teardown_armyx_tech.sh
EOF
