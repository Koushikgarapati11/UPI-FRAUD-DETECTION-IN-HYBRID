#!/bin/bash

# UPI Fraud Detection System - Docker Deployment Script
# Runs the application in a Docker container locally

set -e

echo "🚀 UPI Fraud Detection - Docker Deployment"
echo "=========================================="

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first:"
    echo "   https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if docker-compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install it first:"
    echo "   https://docs.docker.com/compose/install/"
    exit 1
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file..."
    cat > .env << EOF
# Email Configuration (Optional)
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password
ADMIN_EMAIL=admin@securepay.local
EOF
    echo "✓ .env file created. Please update with your email configuration."
fi

# Build the Docker image
echo "📦 Building Docker image..."
docker-compose build

# Run the container
echo "🐳 Starting SecurePay container..."
docker-compose up -d

# Wait for the service to be ready
echo "⏳ Waiting for service to start..."
sleep 5

# Check service health
echo "🔍 Checking service health..."
if docker-compose logs | grep -q "Running on"; then
    echo "✅ SecurePay is now running!"
    echo ""
    echo "📍 Access the application at: http://localhost:5000"
    echo ""
    echo "📋 Commands:"
    echo "   View logs:   docker-compose logs -f"
    echo "   Stop:        docker-compose down"
    echo "   Clean up:    docker-compose down -v"
    echo ""
else
    echo "⚠️  Service may still be starting. Check logs:"
    echo "   docker-compose logs -f"
fi
