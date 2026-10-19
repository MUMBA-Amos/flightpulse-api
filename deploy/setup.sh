#!/usr/bin/env bash
# Installs the FlightPulse API as a service behind nginx (API only; the
# website is hosted on Vercel).
# Run on the server from anywhere: bash ~/flightpulse-api/deploy/setup.sh
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Locking down secrets"
# Only the ubuntu user (which runs the API) may read the Databricks token.
chmod 600 "$DEPLOY_DIR/../.env"

echo "==> Installing Python packages"
# Keeps the server in step with requirements.txt, so a new package can't
# leave the restarted API unable to start.
APP_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
if [ ! -x "$APP_DIR/.venv/bin/pip" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --requirement "$APP_DIR/requirements.txt"

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

echo "==> Configuring monitoring (Grafana Alloy)"
# Sends metrics and logs to Grafana Cloud. Monitoring must never block a
# deploy, so any problem here only prints a warning. (set -e doesn't apply
# inside a function called with ||, hence the explicit "|| return 1".)
setup_alloy() {
  if ! command -v alloy >/dev/null; then
    sudo mkdir -p /etc/apt/keyrings || return 1
    curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor \
      | sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null || return 1
    echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
      | sudo tee /etc/apt/sources.list.d/grafana.list > /dev/null || return 1
    sudo apt-get update || return 1
    sudo apt-get install -y alloy || return 1
  fi
  # The token lives only on the server, like .env.
  if ! sudo grep -q '^GRAFANA_CLOUD_TOKEN=.' /etc/default/alloy; then
    echo "WARNING: no GRAFANA_CLOUD_TOKEN in /etc/default/alloy; monitoring is off."
    return 0
  fi
  # Lets Alloy read the journal and nginx's log.
  sudo usermod -aG adm,systemd-journal alloy || return 1
  sudo cp "$DEPLOY_DIR/alloy-config.alloy" /etc/alloy/config.alloy || return 1
  sudo systemctl enable alloy || return 1
  sudo systemctl restart alloy
}
setup_alloy || echo "WARNING: monitoring setup failed; the API deploy continues."

# The website used to be served from here too; it now lives on Vercel.
sudo rm -rf /var/www/flightpulse "$HOME/flightpulse-web"

# Checks the API itself is up, not Databricks: the data may be temporarily
# unavailable (e.g. its free daily limit), which a deploy can't fix.
echo "==> Checking the API"
sleep 3
if curl -fsS --max-time 30 http://localhost/api/; then
  echo
  echo "==> Done. The API is live at http://$(curl -fsS --max-time 5 https://checkip.amazonaws.com || echo YOUR-IP)/api/"
else
  echo
  echo "==> The API did not answer. Recent logs:"
  sudo journalctl -u flightpulse-api -n 30 --no-pager
  exit 1
fi
