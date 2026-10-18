# Deploying to Azure (beginner guide)

You need: an Azure subscription, and the Azure CLI installed (https://learn.microsoft.com/cli/azure/install-azure-cli). You do **not** need Docker — Azure builds the image.

## 1. Deploy the app (no AI model yet)

```bash
az login
bash infra/deploy-azure.sh
```

Optional variables: `LOCATION` (default `southeastasia`), `RESOURCE_GROUP`, `APP_NAME`. The script:

1. Lists what it will create and asks you to type `yes`.
2. Runs `az containerapp up --source .` (creates the resource group, a Container Apps environment, an Azure Container Registry and a Log Analytics workspace, builds the image in Azure and deploys it).
3. Sets 1–3 replicas so judges never hit a cold start.
4. Calls `https://<app>/api/health` and prints the public URL only if it answers.

**Costs:** Container Apps consumption plan (monthly free grant, then per use; one always-on replica is billed), Container Registry Basic (small monthly fee), Log Analytics (per GB). Remove everything after judging (the rules require the demo to stay available until **10 November 2026**):

```bash
az group delete --name rg-matchpulse --yes
```

## 2. Connect Microsoft Foundry (optional, recommended for the Foundry category)

1. Go to https://ai.azure.com → **Create project**. Pick the same region if possible.
2. In the project, open **Models + endpoints → Deploy model**, choose a chat model available in your region (for example a `gpt-4.1-mini` class model), and note the **deployment name**.
3. On the project **Overview** page, copy the **project endpoint** (`https://<resource>.services.ai.azure.com/api/projects/<project>`).
4. Find the Foundry resource ID: Azure portal → your Foundry resource → **Properties → Resource ID**.
5. Re-run the script with the three values:

```bash
FOUNDRY_PROJECT_ENDPOINT="https://...services.ai.azure.com/api/projects/..." \
FOUNDRY_MODEL="<deployment-name>" \
FOUNDRY_RESOURCE_ID="/subscriptions/.../providers/Microsoft.CognitiveServices/accounts/..." \
bash infra/deploy-azure.sh
```

This enables a system-assigned **managed identity** on the container app, grants it **Azure AI User** on the Foundry resource (least privilege for calling models) and sets the two environment variables. No API key is created or stored.

6. Verify: open `https://<app>/api/health` — `writer.mode` must be `foundry`. Then run an analysis with writer **Auto** and check that the story footer says `Written by: foundry:<model>`. Role assignments can take a few minutes to apply; if you see a writer error banner, wait and retry.

## Run Foundry locally

```bash
az login
cp .env.example .env    # fill FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL
set -a; source .env; set +a
uvicorn matchpulse.api:app --port 8000
```

Your own account needs the **Azure AI User** role on the Foundry project.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `az containerapp up` fails on registry | Re-run; first-time provider registration can be slow (`az provider register -n Microsoft.App`) |
| Health check times out | `az containerapp logs show -n matchpulse -g rg-matchpulse --follow` |
| Banner: "Model writer unavailable … 401/403" | Role assignment missing or still propagating |
| Banner: "… 404 deployment" | `FOUNDRY_MODEL` must be the deployment name, not the model family |
