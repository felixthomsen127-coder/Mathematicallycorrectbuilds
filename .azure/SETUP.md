# Deployment Setup & Quick Start Guide

## 📋 Prerequisites Checklist

Before starting deployment, ensure you have:

### Local Environment
- [ ] **Docker** installed and running
  - Windows: Docker Desktop with WSL 2 backend
  - Verify: `docker --version`
- [ ] **Azure CLI** installed
  - Verify: `az --version`
  - If not installed: [Installation Guide](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
- [ ] **Python 3.14** (for local testing)
  - Verify: `python --version`
- [ ] **PowerShell** or **Bash** for running scripts
  - Windows: PowerShell 7+ recommended
  - Verify: `pwsh --version` or `bash --version`
- [ ] **Git** for repository management
  - Verify: `git --version`

### Azure Account
- [ ] Active Azure subscription with contributor access
- [ ] Sufficient quota in target region (eastus or your chosen region)
- [ ] Note your subscription ID: `az account show --query id -o tsv`

### GitHub
- [ ] Code pushed to GitHub repository
- [ ] Repository is public or you have admin access
- [ ] GitHub CLI installed (for environment setup): `gh --version`

---

## 🚀 Quick Start: 5-Phase Deployment

### Phase 1: Setup Azure CLI & Prepare

```powershell
# Authenticate with Azure
az login

# Set default subscription
$SUBSCRIPTION_ID = "your-subscription-id-here"
az account set --subscription $SUBSCRIPTION_ID

# Verify
az account show

# Create resource group
$RESOURCE_GROUP = "mcb-prod"
$LOCATION = "eastus"
az group create --name $RESOURCE_GROUP --location $LOCATION
```

### Phase 2: Containerize Application

```powershell
cd c:\Users\Felix\.vscode\Python projects\Mathematically_correct_builds

# Ensure Docker is running
docker ps

# Build Docker image
docker build -t mcb-app:latest .

# Test locally
docker run -p 5000:5000 `
  -e FLASK_ENV=production `
  -e FLASK_APP=main.py `
  mcb-app:latest

# Verify in browser: http://localhost:5000
# CTRL+C to stop

# Tag image
$REGISTRY_NAME = "acrMcbprod"  # Will be created in next phase
docker tag mcb-app:latest $REGISTRY_NAME.azurecr.io/mcb-app:latest
```

### Phase 3: Provision Azure Infrastructure

```powershell
# Generate Bicep files (templates provided in .azure/iac-rules.copilotmd)
# Note: Copy provided Bicep template from iac-rules.copilotmd to infra/main.bicep

# Validate Bicep
az bicep build --file infra/main.bicep

# Deploy infrastructure
az deployment group create `
  --name mcb-infra `
  --resource-group mcb-prod `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.json

# Capture outputs
$DEPLOYMENT_OUTPUT = az deployment group show `
  --name mcb-infra `
  --resource-group mcb-prod `
  --query properties.outputs

# Extract registry name
$REGISTRY_NAME = ($DEPLOYMENT_OUTPUT | ConvertFrom-Json).registryName.value
$REGISTRY_LOGIN_SERVER = ($DEPLOYMENT_OUTPUT | ConvertFrom-Json).registryLoginServer.value
```

### Phase 4: Build & Push Docker Image to ACR

```powershell
# Authenticate with ACR
az acr login --name $REGISTRY_NAME

# Build image in ACR (recommended for size optimization)
az acr build `
  --registry $REGISTRY_NAME `
  --image mcb-app:latest `
  .

# Alternate: Push pre-built image
docker tag mcb-app:latest "$REGISTRY_LOGIN_SERVER/mcb-app:latest"
docker push "$REGISTRY_LOGIN_SERVER/mcb-app:latest"

# Verify image in registry
az acr repository list --name $REGISTRY_NAME
```

### Phase 5: Deploy to Azure Container Apps

```powershell
# Get Container App details from Bicep deployment
$CONTAINER_APP_NAME = "app-mcb"  # Configured in Bicep
$CONTAINER_APP_RG = "mcb-prod"

# Update Container App with built image
az containerapp update `
  --name $CONTAINER_APP_NAME `
  --resource-group $CONTAINER_APP_RG `
  --image "$REGISTRY_LOGIN_SERVER/mcb-app:latest"

# Get application URL
$APP_URL = az containerapp show `
  --name $CONTAINER_APP_NAME `
  --resource-group $CONTAINER_APP_RG `
  --query 'properties.configuration.ingress.fqdn' -o tsv

Write-Host "Application deployed to: https://$APP_URL"

# Test application
Invoke-WebRequest -Uri "https://$APP_URL/health" -UseBasicParsing
```

---

## 🔐 GitHub Actions CI/CD Setup

### Step 1: Prepare Azure for GitHub Auth

```powershell
# Create separate resource group for pipeline identity
az group create --name mcb-pipeline --location eastus

# Create user-assigned managed identity
az identity create `
  --resource-group mcb-pipeline `
  --name mcb-github-actions-identity

# Get identity details
$IDENTITY_CLIENT_ID = az identity show `
  --resource-group mcb-pipeline `
  --name mcb-github-actions-identity `
  --query clientId -o tsv

$IDENTITY_OBJECT_ID = az identity show `
  --resource-group mcb-pipeline `
  --name mcb-github-actions-identity `
  --query principalId -o tsv

$TENANT_ID = az account show --query tenantId -o tsv

Write-Host "Client ID: $IDENTITY_CLIENT_ID"
Write-Host "Object ID: $IDENTITY_OBJECT_ID"
Write-Host "Tenant ID: $TENANT_ID"
```

### Step 2: Set Up OIDC Federated Credentials

```powershell
$REPO_OWNER = "your-github-org"
$REPO_NAME = "your-repository-name"

# Create federated credentials for each environment
@("dev", "staging", "production") | ForEach-Object {
  $ENV = $_
  az identity federated-credential create `
    --resource-group mcb-pipeline `
    --identity-name mcb-github-actions-identity `
    --name "github-actions-$ENV" `
    --issuer "https://token.actions.githubusercontent.com" `
    --subject "repo:$REPO_OWNER/$REPO_NAME`:environment:$ENV" `
    --audiences api://AzureADTokenExchange
}
```

### Step 3: Assign RBAC Roles

```powershell
$SUBSCRIPTION_ID = az account show --query id -o tsv

# Get ACR resource ID
$ACR_ID = az acr show `
  --name $REGISTRY_NAME `
  --resource-group mcb-prod `
  --query id -o tsv

# Assign Contributor role on resource group
az role assignment create `
  --assignee $IDENTITY_OBJECT_ID `
  --role Contributor `
  --scope /subscriptions/$SUBSCRIPTION_ID/resourceGroups/mcb-prod

# Assign AcrPush role on registry
az role assignment create `
  --assignee $IDENTITY_OBJECT_ID `
  --role AcrPush `
  --scope $ACR_ID
```

### Step 4: Configure GitHub Environments

```bash
# Using GitHub CLI
gh repo set-default owner/repo-name

# Create environments
gh api repos/{owner}/{repo}/environments/dev -X POST
gh api repos/{owner}/{repo}/environments/staging -X POST
gh api repos/{owner}/{repo}/environments/production -X POST
```

Navigate to GitHub → Settings → Environments → [Environment] and add these **variables**:
- `AZURE_SUBSCRIPTION_ID`: Your subscription ID
- `AZURE_RESOURCE_GROUP`: mcb-prod
- `AZURE_CLIENT_ID`: Client ID from Step 1
- `AZURE_TENANT_ID`: Tenant ID from Step 1
- `REGISTRY_LOGIN_SERVER`: Registry login server (e.g., acrxyz.azurecr.io)
- `REGISTRY_NAME`: Registry name (e.g., acrxyz)
- `CONTAINER_APP_NAME`: Container App name

### Step 5: Deploy Workflow Files

Copy workflow files from `.github/workflows/` to your repository:
- `build-test.yml` - CI: Build and test on PR
- `deploy-dev.yml` - CD: Deploy to dev on main
- `deploy-prod.yml` - CD: Manual production deployment
- `infra-deploy.yml` - Infrastructure updates

---

## 📊 Monitoring & Validation

### Check Application Logs

```powershell
# Stream logs from Container App
az containerapp logs show `
  --name $CONTAINER_APP_NAME `
  --resource-group $CONTAINER_APP_RG `
  --follow

# View in Application Insights
az monitor app-insights query `
  --app my-app-insights `
  --analytics-query "traces | order by timestamp desc | limit 100"
```

### Verify Deployment Health

```powershell
# Check revision status
az containerapp revision list `
  --name $CONTAINER_APP_NAME `
  --resource-group $CONTAINER_APP_RG

# Check ingress configuration
az containerapp ingress show `
  --name $CONTAINER_APP_NAME `
  --resource-group $CONTAINER_APP_RG

# Test endpoint
curl -I "https://$APP_URL/health"
```

---

## 🛠️ Troubleshooting

### Issue: Docker build fails
**Solution**: Ensure requirements.txt is in project root and all dependencies are compatible
```powershell
python -m pip install -r requirements.txt  # Test locally first
```

### Issue: ACR login fails
**Solution**: Verify managed identity has AcrPull role
```powershell
az role assignment list --assignee $IDENTITY_OBJECT_ID
```

### Issue: Container App fails to start
**Solution**: Check logs and ensure environment variables are set
```powershell
az containerapp logs show --name $CONTAINER_APP_NAME --resource-group $CONTAINER_APP_RG
```

### Issue: GitHub Actions OIDC authentication fails
**Solution**: Verify federated credential configuration
```powershell
az identity federated-credential list `
  --resource-group mcb-pipeline `
  --identity-name mcb-github-actions-identity
```

---

## 📝 Important Files Created

In `.azure/` directory:
- `plan.copilotmd` - Overall deployment plan
- `containerization-plan.copilotmd` - Docker setup details
- `cicd-pipeline-guidance.copilotmd` - GitHub Actions configuration
- `iac-rules.copilotmd` - Bicep best practices & rules
- `progress.copilotmd` - Progress tracking

In `infra/` directory (to be created):
- `main.bicep` - Infrastructure template
- `main.parameters.json` - Parameter values

In `.github/workflows/` directory (to be created):
- `build-test.yml` - Build & test workflow
- `deploy-dev.yml` - Dev deployment
- `deploy-prod.yml` - Production deployment
- `infra-deploy.yml` - Infrastructure deployment

---

## ✅ Deployment Completion Checklist

- [ ] Containerization complete (Dockerfile working locally)
- [ ] Azure infrastructure provisioned (Container Apps, ACR, logs)
- [ ] Docker image pushed to ACR
- [ ] Container App deployed and accessible
- [ ] GitHub Actions workflows configured
- [ ] OIDC federated credentials set up
- [ ] GitHub environments created with approval gates
- [ ] Monitoring (Application Insights, Logs) verified
- [ ] End-to-end CI/CD pipeline tested
- [ ] Documentation updated

---

## 🎓 Next Steps

1. **Execute containerization** (Docker image build & validation)
2. **Set up Azure infrastructure** (Run Bicep deployment)
3. **Deploy application** (Push image and deploy to Container Apps)
4. **Configure CI/CD** (GitHub Actions setup and testing)
5. **Monitor & iterate** (Verify logs, performance, and user feedback)

For detailed instructions on each phase, refer to the corresponding markdown file in `.azure/`

---

**Questions?** Check the troubleshooting section or review the deployment plans for detailed guidance.
