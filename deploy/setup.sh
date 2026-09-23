#!/usr/bin/env bash
# Installs the FlightPulse API as a service behind nginx (API only; the
# website is hosted on Vercel).
# Run on the server from anywhere: bash ~/flightpulse-api/deploy/setup.sh
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Locking down secrets"
# Only the ubuntu user (which runs the API) may read the Databricks token.
chmod 600 "$DEPLOY_DIR/../.env"

echo "==> Installing the API service"
sudo cp "$DEPLOY_DIR/flightpulse-api.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable flightpulse-api
sudo systemctl restart flightpulse-api

echo "==> Configuring nginx"
if ! command -v nginx >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y nginx
fi
sudo cp "$DEPLOY_DIR/nginx-flightpulse-proxy.conf" /etc/nginx/flightpulse-proxy.conf
sudo cp "$DEPLOY_DIR/nginx-flightpulse.conf" /etc/nginx/sites-available/flightpulse
sudo ln -sf /etc/nginx/sites-available/flightpulse /etc/nginx/sites-enabled/flightpulse
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# The website used to be served from here too; it now lives on Vercel.
sudo rm -rf /var/www/flightpulse "$HOME/flightpulse-web"

echo "==> Checking the API (the first request connects to Databricks, so it can take a few seconds)"
sleep 3
if curl -fsS --max-time 60 http://localhost/api/stats/; then
  echo
  echo "==> Done. The API is live at http://$(curl -fsS --max-time 5 https://checkip.amazonaws.com || echo YOUR-IP)/api/"
else
  echo
  echo "==> The API did not answer. Recent logs:"
  sudo journalctl -u flightpulse-api -n 30 --no-pager
  exit 1
fi
