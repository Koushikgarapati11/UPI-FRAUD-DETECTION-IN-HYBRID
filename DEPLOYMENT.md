# 🚀 SecurePay Fraud Detection - Deployment Guide

## Overview
This guide covers deployment options for the SecurePay UPI Fraud Detection System across different environments.

## Local Development

### Prerequisites
- Python 3.8+
- pip
- Git

### Setup

```bash
# Clone/navigate to project
cd upi_fraud_project

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run application
python app.py
```

Access at: http://localhost:5000

## Docker Deployment

### Prerequisites
- Docker
- Docker Compose
- Git

### Quick Start

```bash
# Make deploy script executable
chmod +x deploy-docker.sh

# Run deployment script
./deploy-docker.sh
```

Or manually:

```bash
# Build image
docker-compose build

# Start container
docker-compose up -d

# View logs
docker-compose logs -f

# Stop container
docker-compose down
```

### Configuration

Set environment variables in `.env` file:

```bash
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password
ADMIN_EMAIL=admin@securepay.local
```

## Azure Deployment

### Prerequisites
- Azure CLI (`az` command)
- Docker
- Azure subscription

### Option 1: Azure Container Instances (Simple)

```bash
# Make script executable
chmod +x deploy-azure.sh

# Run deployment
./deploy-azure.sh
```

### Option 2: Azure Container Apps (Recommended)

```bash
# Login to Azure
az login

# Create resource group
az group create --name securepay-rg --location eastus

# Create container registry
az acr create --resource-group securepay-rg \
    --name securepayacr --sku Basic

# Build and push image
az acr build --registry securepayacr \
    --image securepay:latest .

# Create container app
az containerapp create \
    --name securepay-app \
    --resource-group securepay-rg \
    --image securepayacr.azurecr.io/securepay:latest \
    --target-port 5000 \
    --ingress 'external' \
    --registry-server securepayacr.azurecr.io \
    --registry-username <username> \
    --registry-password <password>
```

### Option 3: Azure App Service

```bash
# Create App Service plan
az appservice plan create \
    --name securepay-plan \
    --resource-group securepay-rg \
    --sku B1 --is-linux

# Create web app with Docker image
az webapp create \
    --resource-group securepay-rg \
    --plan securepay-plan \
    --name securepay-app \
    --deployment-container-image-name securepayacr.azurecr.io/securepay:latest
```

### Scaling in Azure
```bash
# Scale Container App
az containerapp update \
    --name securepay-app \
    --resource-group securepay-rg \
    --min-replicas 2 --max-replicas 5
```

## AWS Deployment

### Prerequisites
- AWS CLI
- Docker
- AWS account with appropriate permissions

### Option 1: AWS Elastic Container Service (ECS)

```bash
# Make script executable
chmod +x deploy-aws.sh

# Run deployment
./deploy-aws.sh
```

### Option 2: AWS App Runner (Simplest)

```bash
# Create App Runner service
aws apprunner create-service \
    --service-name securepay \
    --source-configuration '{
        "ImageRepository": {
            "ImageIdentifier": "123456789.dkr.ecr.us-east-1.amazonaws.com/securepay:latest",
            "ImageRepositoryType": "ECR"
        }
    }' \
    --instance-configuration CPUValue=0.5,MemoryValue=1024 \
    --region us-east-1
```

### Option 3: AWS Elastic Beanstalk

```bash
# Initialize Beanstalk
eb init -p docker securepay

# Create environment
eb create securepay-env

# Deploy
eb deploy

# View logs
eb logs
```

### Auto-scaling in AWS
```bash
# Create auto-scaling group
aws autoscaling create-auto-scaling-group \
    --auto-scaling-group-name securepay-asg \
    --launch-configuration securepay-lc \
    --min-size 2 \
    --max-size 5 \
    --desired-capacity 2
```

## GCP Deployment

### Prerequisites
- Google Cloud SDK (`gcloud` command)
- Docker
- GCP project

### Deploy to Cloud Run (Serverless)

```bash
# Set project
gcloud config set project PROJECT_ID

# Build and push image
gcloud builds submit --tag gcr.io/PROJECT_ID/securepay

# Deploy to Cloud Run
gcloud run deploy securepay \
    --image gcr.io/PROJECT_ID/securepay \
    --platform managed \
    --region us-central1 \
    --port 5000 \
    --memory 512Mi \
    --cpu 1 \
    --allow-unauthenticated
```

### Deploy to GKE (Kubernetes)

```bash
# Create cluster
gcloud container clusters create securepay-cluster

# Push image
gcloud docker -- push gcr.io/PROJECT_ID/securepay

# Create deployment
kubectl create deployment securepay \
    --image=gcr.io/PROJECT_ID/securepay

# Expose service
kubectl expose deployment securepay \
    --type=LoadBalancer \
    --port 80 \
    --target-port 5000
```

## Kubernetes Deployment

### Create Deployment Manifest

```yaml
# securepay-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: securepay-fraud-detection
spec:
  replicas: 2
  selector:
    matchLabels:
      app: securepay
  template:
    metadata:
      labels:
        app: securepay
    spec:
      containers:
      - name: securepay
        image: securepay:latest
        ports:
        - containerPort: 5000
        env:
        - name: FLASK_ENV
          value: "production"
        - name: SMTP_SERVER
          value: "smtp.gmail.com"
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /
            port: 5000
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /
            port: 5000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: securepay-service
spec:
  type: LoadBalancer
  selector:
    app: securepay
  ports:
  - protocol: TCP
    port: 80
    targetPort: 5000
```

### Deploy to Kubernetes

```bash
# Create namespace
kubectl create namespace securepay

# Apply manifests
kubectl apply -f securepay-deployment.yaml -n securepay

# Check status
kubectl get pods -n securepay
kubectl get services -n securepay

# View logs
kubectl logs -f deployment/securepay-fraud-detection -n securepay

# Scale deployment
kubectl scale deployment securepay-fraud-detection --replicas=3 -n securepay
```

## Production Checklist

- [ ] Update `app.secret_key` with a secure random string
- [ ] Configure email alerts (SMTP credentials)
- [ ] Set up HTTPS/SSL certificate
- [ ] Configure database backups
- [ ] Set up monitoring and logging
- [ ] Configure rate limiting
- [ ] Enable authentication/CSRF protection
- [ ] Test fraud detection models
- [ ] Set up auto-scaling policies
- [ ] Configure CDN for static files
- [ ] Implement request logging
- [ ] Set up error tracking (e.g., Sentry)

## Performance Optimization

### Database Index Creation
```sql
CREATE INDEX idx_username ON logs(username);
CREATE INDEX idx_created_at ON logs(created_at);
CREATE INDEX idx_score ON logs(score);
CREATE INDEX idx_result ON logs(result);
```

### Caching Strategy
- Cache fraud detection models
- Cache admin statistics (30-minute TTL)
- Use Redis for session management

### Load Balancer Configuration
- Round-robin load distribution
- Health checks every 30 seconds
- Connection timeout: 60 seconds
- Maximum connections per instance: 100

## Monitoring

### Metrics to Track
- Request latency (p50, p95, p99)
- Fraud detection accuracy
- Database query performance
- Error rates
- CPU/Memory usage
- Transaction throughput

### Logging
- All transactions logged with score and result
- Authentication attempts logged
- Error tracking with stack traces
- Access logs with IP and user agent

## Troubleshooting

### Container won't start
```bash
# Check logs
docker logs <container-id>

# Verify image
docker image ls

# Test locally
docker run -it securepay:latest sh
```

### Database connection issues
```bash
# Check database file exists and is readable
ls -la /app/database.db

# Verify SQLite is working
sqlite3 /app/database.db ".tables"
```

### Email alerts not sending
- Verify SMTP credentials in environment variables
- Check firewall allows SMTP port 587
- Enable "Less secure app access" for Gmail
- Check logs for SMTP errors

## Rollback Procedures

### Docker Compose
```bash
docker-compose down
docker-compose up -d
```

### Kubernetes
```bash
# View rollout history
kubectl rollout history deployment/securepay

# Rollback to previous version
kubectl rollout undo deployment/securepay

# Rollback to specific version
kubectl rollout undo deployment/securepay --to-revision=2
```

### Azure
```bash
# Update image in container app
az containerapp update \
    --name securepay-app \
    --resource-group securepay-rg \
    --image securepayacr.azurecr.io/securepay:previous-tag
```

## Support & Documentation

- **Local Setup Issues**: Check Python version compatibility
- **Docker Issues**: Ensure Docker daemon is running
- **Cloud Deployment**: Verify credentials and permissions
- **Database Issues**: Check file permissions and disk space
- **Email Issues**: Verify SMTP configuration and firewall rules

---

**Last Updated**: 2024
**Version**: 1.0
