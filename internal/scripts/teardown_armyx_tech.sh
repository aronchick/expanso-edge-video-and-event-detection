#!/usr/bin/env bash
# Tear down everything created by bootstrap_armyx_tech.sh.
# Refuses to run unless --yes is passed: this is destructive.
#
# Removes (in order):
#   - Expanso pipeline job armyx-tech-event-archive (if deployed)
#   - All access keys for IAM user armyx-tech-edge
#   - The inline policy armyx-tech-edge-policy
#   - The IAM user armyx-tech-edge
#   - The S3 bucket armyx-tech-edge-events-<account-id>  (with --delete-bucket)
#   - The [armyx-tech] profile in ~/.aws/credentials  (with --clean-mac)
#   - The [armyx-tech] profile on the Jetson         (with --clean-jetson)
#   - Project .env entries                           (with --clean-env)
#
# By default the bucket and credential profiles are LEFT ALONE so a re-bootstrap
# is fast. Pass the appropriate flags below to nuke each surface.

set -euo pipefail

PROJECT="armyx-tech"
EXPANSO_PROFILE="armyx-tech"
PIPELINE_NAME="armyx-tech-event-archive"
USER_NAME="${PROJECT}-edge"
POLICY_NAME="${PROJECT}-edge-policy"
REGION="${AWS_REGION:-us-west-2}"

CONFIRMED=0
DELETE_BUCKET=0
CLEAN_MAC=0
CLEAN_JETSON=0
CLEAN_ENV=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) CONFIRMED=1 ;;
    --delete-bucket) DELETE_BUCKET=1 ;;
    --clean-mac) CLEAN_MAC=1 ;;
    --clean-jetson) CLEAN_JETSON=1 ;;
    --clean-env) CLEAN_ENV=1 ;;
    --all) CONFIRMED=1; DELETE_BUCKET=1; CLEAN_MAC=1; CLEAN_JETSON=1; CLEAN_ENV=1 ;;
    --help|-h) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

if [[ "$CONFIRMED" -ne 1 ]]; then
  echo "Refusing to run without --yes. Use --help for flag list."
  exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${PROJECT}-edge-events-${ACCOUNT_ID}"

# ---------- expanso pipeline ----------
if command -v expanso-cli >/dev/null 2>&1; then
  if expanso-cli job describe "$PIPELINE_NAME" >/dev/null 2>&1; then
    expanso-cli job stop "$PIPELINE_NAME" >/dev/null 2>&1 || true
    echo "OK    stopped expanso pipeline ${PIPELINE_NAME}"
  fi
fi

# ---------- IAM ----------
if aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  for kid in $(aws iam list-access-keys --user-name "$USER_NAME" \
        --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
    aws iam delete-access-key --user-name "$USER_NAME" --access-key-id "$kid"
    echo "OK    deleted access key ${kid}"
  done
  aws iam delete-user-policy --user-name "$USER_NAME" --policy-name "$POLICY_NAME" 2>/dev/null || true
  aws iam delete-user --user-name "$USER_NAME"
  echo "OK    deleted IAM user ${USER_NAME}"
else
  echo "INFO  IAM user ${USER_NAME} not present, skipping"
fi

# ---------- bucket ----------
if [[ "$DELETE_BUCKET" -eq 1 ]]; then
  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "INFO  emptying s3://${BUCKET} (versioned)"
    aws s3api list-object-versions --bucket "$BUCKET" \
      --query '{Objects: Versions[].{Key: Key, VersionId: VersionId}}' \
      --output json > /tmp/armyx-versions.json 2>/dev/null || echo '{"Objects":[]}' > /tmp/armyx-versions.json
    if [[ "$(jq '.Objects | length // 0' /tmp/armyx-versions.json)" -gt 0 ]]; then
      aws s3api delete-objects --bucket "$BUCKET" --delete file:///tmp/armyx-versions.json >/dev/null
    fi
    aws s3api list-object-versions --bucket "$BUCKET" \
      --query '{Objects: DeleteMarkers[].{Key: Key, VersionId: VersionId}}' \
      --output json > /tmp/armyx-markers.json 2>/dev/null || echo '{"Objects":[]}' > /tmp/armyx-markers.json
    if [[ "$(jq '.Objects | length // 0' /tmp/armyx-markers.json)" -gt 0 ]]; then
      aws s3api delete-objects --bucket "$BUCKET" --delete file:///tmp/armyx-markers.json >/dev/null
    fi
    aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION"
    echo "OK    deleted bucket s3://${BUCKET}"
    rm -f /tmp/armyx-versions.json /tmp/armyx-markers.json
  fi
else
  echo "INFO  bucket s3://${BUCKET} retained (pass --delete-bucket to remove)"
fi

# ---------- Mac credentials ----------
if [[ "$CLEAN_MAC" -eq 1 && -f "$HOME/.aws/credentials" ]]; then
  python3 - <<PY
import re, pathlib
p = pathlib.Path.home() / ".aws" / "credentials"
text = p.read_text()
out = re.sub(r"(?m)^\[${PROJECT}\][^\[]*?(?=^\[|\Z)", "", text)
p.write_text(out.rstrip() + "\n")
PY
  echo "OK    removed [${PROJECT}] profile from Mac ~/.aws/credentials"
fi

# ---------- Jetson credentials ----------
if [[ "$CLEAN_JETSON" -eq 1 && -n "${JETSON_HOST:-}" ]]; then
  ssh "$JETSON_HOST" "python3 -c \"
import re, pathlib
p = pathlib.Path.home() / '.aws' / 'credentials'
if p.exists():
    text = p.read_text()
    out = re.sub(r'(?m)^\\\\[${PROJECT}\\\\][^\\\\[]*?(?=^\\\\[|\\\\Z)', '', text)
    p.write_text(out.rstrip() + chr(10))
\"" || true
  echo "OK    removed [${PROJECT}] profile from Jetson ~/.aws/credentials"
fi

# ---------- project .env ----------
if [[ "$CLEAN_ENV" -eq 1 && -f .env ]]; then
  sed -i.bak '/^ARMYX_/d' .env && rm -f .env.bak
  echo "OK    removed ARMYX_* entries from .env"
fi

# ---------- secrets dir ----------
if [[ -d ".${PROJECT}-secrets" ]]; then
  rm -rf ".${PROJECT}-secrets"
  echo "OK    removed local secrets dir"
fi

echo ""
echo "=== teardown done ==="
