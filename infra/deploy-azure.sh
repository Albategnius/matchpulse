#!/usr/bin/env bash
# Deploy MATCHPULSE to Azure Container Apps (builds the image in Azure — no local Docker needed).
# Usage:  bash infra/deploy-azure.sh
# Optional Foundry wiring:  FOUNDRY_PROJECT_ENDPOINT=... FOUNDRY_MODEL=... FOUNDRY_RESOURCE_ID=... bash infra/deploy-azure.sh
set -euo pipefail

APP_NAME="${APP_NAME:-matchpulse}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-matchpulse}"
LOCATION="${LOCATION:-southeastasia}"
ENV_NAME="${ENV_NAME:-matchpulse-env}"

command -v az >/dev/null || { echo "Azure CLI not found. Install: https://learn.microsoft.com/cli/azure/install-azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || az login
az extension add --name containerapp --upgrade --only-show-errors

SUB=$(az account show --query name -o tsv)
cat <<EOF

About to create or update in subscription: $SUB
  Resource group:          $RESOURCE_GROUP ($LOCATION)
  Container Apps env/app:  $ENV_NAME / $APP_NAME   (consumption plan; monthly free grant, then pay per use)
  Azure Container Registry (Basic, created by 'containerapp up'; small monthly charge)
  Log Analytics workspace  (pay per GB ingested; tiny for this app)
These resources MAY INCUR CHARGES. Delete everything later with:
  az group delete --name $RESOURCE_GROUP --yes
EOF
read -r -p "Type 'yes' to continue: " OK
[ "$OK" = "yes" ] || { echo "Cancelled."; exit 0; }

cd "$(dirname "$0")/.."

az containerapp up \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --environment "$ENV_NAME" \
  --source . \
  --ingress external \
  --target-port 8000

# Keep one replica warm so judges never hit a cold start, cap at 3.
az containerapp update -n "$APP_NAME" -g "$RESOURCE_GROUP" --min-replicas 1 --max-replicas 3 --only-show-errors >/dev/null

if [ -n "${FOUNDRY_PROJECT_ENDPOINT:-}" ] && [ -n "${FOUNDRY_MODEL:-}" ]; then
  echo "Wiring Microsoft Foundry with a managed identity (no API keys)..."
  PRINCIPAL=$(az containerapp identity assign -n "$APP_NAME" -g "$RESOURCE_GROUP" --system-assigned --query principalId -o tsv)
  if [ -n "${FOUNDRY_RESOURCE_ID:-}" ]; then
    az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
      --role "Azure AI User" --scope "$FOUNDRY_RESOURCE_ID" --only-show-errors >/dev/null \
      || echo "Role assignment failed — assign 'Azure AI User' on the Foundry resource manually (see docs/DEPLOY_AZURE.md)."
  else
    echo "FOUNDRY_RESOURCE_ID not set: assign the 'Azure AI User' role to principal $PRINCIPAL manually."
  fi
  az containerapp update -n "$APP_NAME" -g "$RESOURCE_GROUP" --only-show-errors \
    --set-env-vars FOUNDRY_PROJECT_ENDPOINT="$FOUNDRY_PROJECT_ENDPOINT" FOUNDRY_MODEL="$FOUNDRY_MODEL" >/dev/null
fi

FQDN=$(az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
URL="https://$FQDN"
echo "Checking health at $URL/api/health ..."
for i in $(seq 1 20); do
  if BODY=$(curl -fsS "$URL/api/health" 2>/dev/null); then
    echo "HEALTHY: $BODY"
    echo "Public demo URL: $URL"
    exit 0
  fi
  sleep 10
done
echo "Deployment finished but the health check did not pass yet. Logs:"
echo "  az containerapp logs show -n $APP_NAME -g $RESOURCE_GROUP --follow"
exit 1
