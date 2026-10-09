#!/usr/bin/with-contenv bashio
set -e

# Options are read from /data/options.json by maverick.py itself; only the MQTT
# broker (a Supervisor service, not an option) is handed over here.
if bashio::services.available "mqtt"; then
  export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
  export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
  export MQTT_USER="$(bashio::services 'mqtt' 'username')"
  export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
else
  bashio::log.warning "No MQTT broker found - results will only be on the add-on page, not as entities."
fi

bashio::log.info "Starting Maverick HVAC"
cd /app
exec python3 /app/maverick.py
