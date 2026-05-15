#!/bin/bash

# UPI Fraud Detection System - AWS EC2/ECS Deployment
# Deploys using AWS Elastic Container Service (ECS)

set -e

AWS_REGION="us-east-1"
ECR_REPO_NAME="securepay-fraud-detection"
CLUSTER_NAME="securepay-cluster"
SERVICE_NAME="securepay-service"
TASK_FAMILY="securepay-task"

echo "☁️  UPI Fraud Detection - AWS Deployment"
echo "======================================"

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI is not installed. Install from:"
    echo "   https://aws.amazon.com/cli/"
    exit 1
fi

# Set AWS region
export AWS_DEFAULT_REGION=$AWS_REGION

# Create ECR repository
echo "📦 Creating ECR repository: $ECR_REPO_NAME"
ECR_URL=$(aws ecr create-repository \
    --repository-name "$ECR_REPO_NAME" \
    --region "$AWS_REGION" \
    --query 'repository.repositoryUri' \
    --output text) || echo "Repository may already exist"

if [ -z "$ECR_URL" ]; then
    ECR_URL=$(aws ecr describe-repositories \
        --repository-names "$ECR_REPO_NAME" \
        --region "$AWS_REGION" \
        --query 'repositories[0].repositoryUri' \
        --output text)
fi

echo "📍 ECR URL: $ECR_URL"

# Login to ECR
echo "🔐 Logging in to ECR..."
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "$ECR_URL"

# Build and push Docker image
echo "🔨 Building and pushing Docker image..."
docker build -t securepay:latest .
docker tag securepay:latest "${ECR_URL}:latest"
docker push "${ECR_URL}:latest"

# Create ECS cluster (if not exists)
echo "🏗️  Creating ECS cluster: $CLUSTER_NAME"
aws ecs create-cluster --cluster-name "$CLUSTER_NAME" || echo "Cluster may already exist"

# Register task definition
echo "📝 Registering ECS task definition..."
cat > task-def.json << EOF
{
  "family": "$TASK_FAMILY",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "containerDefinitions": [
    {
      "name": "$CONTAINER_IMAGE",
      "image": "${ECR_URL}:latest",
      "portMappings": [
        {
          "containerPort": 5000,
          "hostPort": 5000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "FLASK_ENV",
          "value": "production"
        },
        {
          "name": "SMTP_SERVER",
          "value": "smtp.gmail.com"
        },
        {
          "name": "SMTP_PORT",
          "value": "587"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/securepay",
          "awslogs-region": "$AWS_REGION",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
EOF

aws ecs register-task-definition --cli-input-json file://task-def.json

echo "✅ Deployment preparation complete!"
echo ""
echo "📋 Next steps:"
echo "1. Create a VPC and security groups (if not done)"
echo "2. Create a load balancer (Application Load Balancer)"
echo "3. Create an ECS service:"
echo ""
echo "   aws ecs create-service \\"
echo "     --cluster $CLUSTER_NAME \\"
echo "     --service-name $SERVICE_NAME \\"
echo "     --task-definition $TASK_FAMILY:1 \\"
echo "     --desired-count 1 \\"
echo "     --launch-type FARGATE"
echo ""
echo "4. Configure load balancer target groups and listeners"
echo "5. Access application via load balancer DNS name"
