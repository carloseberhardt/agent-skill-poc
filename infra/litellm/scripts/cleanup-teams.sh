#!/bin/bash
# Removes all teams, keys, and models from LiteLLM
# Useful for resetting the POC environment

LITELLM_URL="${LITELLM_URL:-http://localhost:4000}"
MASTER_KEY="${LITELLM_MASTER_KEY:-sk-master-key-change-me}"

echo "=============================================="
echo "LiteLLM Cleanup"
echo "=============================================="
echo "URL: $LITELLM_URL"
echo ""

# Check if LiteLLM is running
if ! curl -s "$LITELLM_URL/health" > /dev/null 2>&1; then
  echo "ERROR: LiteLLM is not running at $LITELLM_URL"
  exit 1
fi

# ============================================
# Delete all keys
# ============================================
echo "Fetching all keys..."
KEY_RESPONSE=$(curl -s "$LITELLM_URL/key/list" -H "Authorization: Bearer $MASTER_KEY")
KEY_COUNT=$(echo "$KEY_RESPONSE" | jq -r '.total_count // 0')

if [ "$KEY_COUNT" -gt 0 ]; then
  echo "Deleting $KEY_COUNT keys..."
  TOKENS=$(echo "$KEY_RESPONSE" | jq -r '.keys[]')
  for TOKEN in $TOKENS; do
    echo "  Deleting key: ${TOKEN:0:20}..."
    curl -s -X POST "$LITELLM_URL/key/delete" \
      -H "Authorization: Bearer $MASTER_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"keys\": [\"$TOKEN\"]}" > /dev/null
  done
  echo "  Done."
else
  echo "  No keys found."
fi

# ============================================
# Delete all teams
# ============================================
echo ""
echo "Fetching all teams..."
TEAMS=$(curl -s "$LITELLM_URL/team/list" \
  -H "Authorization: Bearer $MASTER_KEY" | jq -r '.[]?.team_id // empty')

if [ -n "$TEAMS" ]; then
  echo "Deleting teams..."
  for TEAM_ID in $TEAMS; do
    echo "  Deleting team: $TEAM_ID"
    curl -s -X POST "$LITELLM_URL/team/delete" \
      -H "Authorization: Bearer $MASTER_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"team_ids\": [\"$TEAM_ID\"]}" > /dev/null
  done
  echo "  Done."
else
  echo "  No teams found."
fi

# ============================================
# Delete all models
# ============================================
echo ""
echo "Fetching all models..."
MODELS=$(curl -s "$LITELLM_URL/model/info" \
  -H "Authorization: Bearer $MASTER_KEY" | jq -r '.data[]?.model_info?.id // empty')

if [ -n "$MODELS" ]; then
  echo "Deleting models..."
  for MODEL_ID in $MODELS; do
    echo "  Deleting model: $MODEL_ID"
    curl -s -X POST "$LITELLM_URL/model/delete" \
      -H "Authorization: Bearer $MASTER_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"id\": \"$MODEL_ID\"}" > /dev/null
  done
  echo "  Done."
else
  echo "  No models found."
fi

# Clean up local env file
if [ -f /tmp/litellm-keys.env ]; then
  rm /tmp/litellm-keys.env
  echo ""
  echo "Removed /tmp/litellm-keys.env"
fi

echo ""
echo "=============================================="
echo "Cleanup complete!"
echo "=============================================="
echo ""
echo "Run ./scripts/setup-teams.sh to recreate everything."
echo ""
