# Deployment Documentation

## Overview
Deployment guide for the ICT Trading Bot on Oracle Cloud Infrastructure.

## Prerequisites
- Oracle Cloud VM (Ubuntu 20.04+)
- Docker and Docker Compose installed
- Python 3.8+
- Git
- SSL certificate (Let's Encrypt)

## Environment Setup
1. Clone the repository
2. Copy `.env.example` to `.env`
3. Set API keys and credentials
4. Install dependencies: `pip install -r requirements.txt`

## Deployment Steps
1. Build Docker image: `docker build -t ict-bot .`
2. Run container: `docker-compose up -d`
3. Verify: `docker ps` should show running bot
4. Check logs: `docker logs -f ict-bot`

## Service Files
- `deploy/bot.service` - Systemd service file
- `deploy/nginx.conf` - Nginx reverse proxy config
- `deploy/ssl-renew.sh` - SSL renewal script

## Health Checks
- Bot heartbeat endpoint: `/health`
- Telegram bot response test
- Exchange API connection test

## Rollback Procedure
1. Stop current container: `docker-compose down`
2. Pull previous image tag
3. Restart with previous version

## Monitoring
- Prometheus metrics endpoint
- Grafana dashboards
- Alertmanager notifications

## Backup
- Daily database backup
- Config file backup
- Strategy parameters export
