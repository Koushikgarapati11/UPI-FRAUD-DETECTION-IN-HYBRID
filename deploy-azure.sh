#!/bin/bash

# UPI Fraud Detection System - Azure Container Instances Deployment
# Deploys to Azure Container Instances (ACI)

set -e

RESOURCE_GROUP="securepay-rg"
CONTAINER_REGISTRY="securepayacr"
LOCATION="eastus"
CONTAINER_NAME="securepay-fraud-detection"
IMAGE_NAME="securepay:latest"
ACR_URL="${CONTAINER_REGISTRY}.azurecr.io"

echo "☁️  UPI Fraud Detection - Azure Deployment"
echo "========================================"

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    echo "❌ Azure CLI is not installed. Install from:"
    echo "   https://docs.microsoft.com/cli/azure/install-azure-cli"
    exit 1
fi

# Login to Azure
echo "🔐 Logging in to Azure..."
az login

# Create resource group
echo "📦 Creating resource group: $RESOURCE_GROUP"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"

# Create container registry
echo "🏗️  Creating container registry: $CONTAINER_REGISTRY"
az acr create --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_REGISTRY" --sku Basic

# Build and push image to ACR
echo "🔨 Building and pushing Docker image to ACR..."
az acr build --registry "$CONTAINER_REGISTRY" \
    --image "$IMAGE_NAME" .

# Get ACR login credentials
echo "🔑 Getting ACR credentials..."
REGISTRY_PASSWORD=$(az acr credential show --name "$CONTAINER_REGISTRY" \
    --query "passwords[0].value" -o tsv)

# Deploy to Azure Container Instances
echo "🚀 Deploying to Azure Container Instances..."
az container create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_NAME" \
    --image "${ACR_URL}/${IMAGE_NAME}" \
    --cpu 1 \
    --memory 1 \
    --registry-login-server "$ACR_URL" \
    --registry-username "$CONTAINER_REGISTRY" \
    --registry-password "$REGISTRY_PASSWORD" \
    --ports 5000 \
    --environment-variables \
        FLASK_ENV=production \
        SMTP_SERVER=smtp.gmail.com \
        SMTP_PORT=587 \
    --dns-name-label "securepay-$(date +%s)" \
    --restart-policy OnFailure

# Get container details
echo "✅ Deployment complete!"
CONTAINER_INFO=$(az container show --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_NAME")

FQDN=$(echo "$CONTAINER_INFO" | jq -r '.ipAddress.fqdn')
echo ""
echo "📍 Application URL: http://${FQDN}:5000"
echo ""
echo "📋 Useful commands:"
echo "   View logs:    az container logs --name $CONTAINER_NAME --resource-group $RESOURCE_GROUP"
echo "   Stop:         az container stop --name $CONTAINER_NAME --resource-group $RESOURCE_GROUP"
echo "   Delete:       az container delete --name $CONTAINER_NAME --resource-group $RESOURCE_GROUP"
