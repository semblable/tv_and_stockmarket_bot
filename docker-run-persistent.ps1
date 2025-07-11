#!/usr/bin/env pwsh

# PowerShell script to run containers with persistent data
# Usage: .\docker-run-persistent.ps1

Write-Host "🐳 Starting Docker containers with persistent data..." -ForegroundColor Blue

# Stop existing containers
Write-Host "Stopping existing containers..." -ForegroundColor Yellow
docker stop bot-container dashboard-container 2>$null
docker rm bot-container dashboard-container 2>$null

# Ensure the network exists
Write-Host "Ensuring app-network exists..." -ForegroundColor Yellow
docker network create app-network 2>$null

# Create data directory if it doesn't exist
if (!(Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" -Force
}

# Start bot container with improved network settings
Write-Host "Starting bot container with improved networking..." -ForegroundColor Green
docker run -d `
    --name bot-container `
    --network app-network `
    --dns 8.8.8.8 `
    --dns 8.8.4.4 `
    -p 5000:5000 `
    -v "${PWD}\data:/app/data" `
    discord-bot

# Start dashboard container
Write-Host "Starting dashboard container..." -ForegroundColor Green
docker run -d `
    --name dashboard-container `
    --network app-network `
    -p 8050:8000 `
    -v "${PWD}\data:/app/data" `
    project-dashboard

# Show container status
Write-Host "`n🐳 Container Status:" -ForegroundColor Green
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

Write-Host ""
Write-Host "✅ Containers started with persistent data!" -ForegroundColor Green
Write-Host "💾 Database: data/app.db (persistent)"
Write-Host "🌐 Dashboard: http://localhost:8050"
Write-Host "🤖 Bot API: http://localhost:5000" 