#!/usr/bin/env pwsh

# PowerShell script to run containers with persistent data
# Usage: .\docker-run-persistent.ps1
# Optional: Set $env:BOT_DNS to use custom DNS (e.g., "8.8.8.8")

Write-Host "🐳 Starting Docker containers with persistent data..." -ForegroundColor Blue

# Stop existing containers
Write-Host "Stopping existing containers..." -ForegroundColor Yellow
docker stop bot-container 2>$null
docker rm bot-container 2>$null

# Ensure the network exists
Write-Host "Ensuring app-network exists..." -ForegroundColor Yellow
docker network create app-network 2>$null

# Create data directory if it doesn't exist
if (!(Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" -Force
}

# Prepare DNS arguments if BOT_DNS environment variable is set
$dnsArgs = @()
if ($env:BOT_DNS) {
    Write-Host "Using custom DNS: $env:BOT_DNS" -ForegroundColor Cyan
    $dnsArgs += "--dns", $env:BOT_DNS
}

# Start bot container
Write-Host "Starting bot container..." -ForegroundColor Green
docker run -d `
    --name bot-container `
    --network app-network `
    @dnsArgs `
    -p 5000:5000 `
    -v "${PWD}\data:/app/data" `
    discord-bot

# Show container status
Write-Host "`n🐳 Container Status:" -ForegroundColor Green
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

Write-Host ""
Write-Host "✅ Containers started with persistent data!" -ForegroundColor Green
Write-Host "💾 Database: data/app.db (persistent)"
Write-Host "🤖 Bot API: http://localhost:5000"
