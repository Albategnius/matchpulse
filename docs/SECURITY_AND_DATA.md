# Security and data provenance

## Data provenance

- Every event is produced by `matchpulse/synth.py` from a scenario definition and a random seed. Same seed → byte-identical dataset.
- Team names (Northbridge Athletic, Harbour City Rovers) and player IDs (`H01`–`H11`, `A01`–`A11`) are fictional.
- No Premier League data, footage, logos, club names, player names or other third-party data is used, bundled or fetched.
- Every export carries a SYNTHETIC DATA disclaimer (CSV comment line, JSON `disclaimer` field, overlay `synthetic: true`), and the UI shows it permanently.
- Uploaded CSVs are processed in memory for the request and not stored.

## Secrets and identity

- No API keys anywhere. Foundry access uses Microsoft Entra ID through `DefaultAzureCredential`: `az login` locally, a system-assigned managed identity in Azure Container Apps.
- The managed identity receives only the **Azure AI User** role scoped to the Foundry resource.
- `.env` is git-ignored; `.env.example` contains no values.
- The container runs as an unprivileged user (uid 10001).

## Application safeguards

- Request validation with Pydantic (bounded seed, window size, 5 MB CSV limit).
- The language model receives only verified claims and evidence values; it cannot introduce claims or change evidence, and every number it writes is verified before display.
- The model is instructed never to reference real clubs, players or the Premier League; the reviewer rejects team names outside the match.
- All model failures degrade to deterministic output; errors never expose stack traces to the client.

## Licensing

MIT License for this repository. Dependencies: Microsoft Agent Framework (MIT), FastAPI (MIT), Uvicorn (BSD-3), Pydantic (MIT), Azure Identity (MIT).
