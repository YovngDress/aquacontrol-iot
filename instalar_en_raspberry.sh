#!/bin/bash
# Instala la plataforma completa en la Raspberry Pi, sin Docker:
#   Mosquitto (broker MQTT) + Django (dashboard) + listener MQTT, con SQLite.
# Quedan como servicios: arrancan solos al encender la Raspberry.
#
# Uso (dentro de la carpeta aquacontrol-iot):
#   bash instalar_en_raspberry.sh                 # clave MQTT: Agua2026, admin: Admin2026!
#   bash instalar_en_raspberry.sh MiClaveMQTT MiClaveAdmin
set -e

MQTT_USER=planta
MQTT_PASS=${1:-Agua2026}
ADMIN_PASS=${2:-Admin2026!}
DIR=$(cd "$(dirname "$0")" && pwd)
ME=$(whoami)

echo "== 1/5 Instalando Mosquitto y Python =="
sudo apt-get update -y
sudo apt-get install -y mosquitto mosquitto-clients python3-venv

echo "== 2/5 Configurando Mosquitto (usuario: $MQTT_USER) =="
sudo mosquitto_passwd -c -b /etc/mosquitto/passwd "$MQTT_USER" "$MQTT_PASS"
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
sudo chmod 600 /etc/mosquitto/passwd
printf "listener 1883 0.0.0.0\nallow_anonymous false\npassword_file /etc/mosquitto/passwd\n" \
  | sudo tee /etc/mosquitto/conf.d/aquacontrol.conf >/dev/null
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto

echo "== 3/5 Instalando Django =="
cat > "$DIR/plataforma.env" <<EOF
DB_ENGINE=sqlite
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=*
DJANGO_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(40))')
MQTT_BROKER_HOST=127.0.0.1
MQTT_BROKER_PORT=1883
MQTT_USERNAME=$MQTT_USER
MQTT_PASSWORD=$MQTT_PASS
MQTT_TOPIC_PREFIX=planta
EOF
cd "$DIR/backend"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install "Django==5.0.9" "django-environ==0.11.2" "whitenoise==6.7.0" "paho-mqtt==1.6.1" Pillow

echo "== 4/5 Base de datos, usuario admin y dispositivos =="
set -a; source "$DIR/plataforma.env"; set +a
.venv/bin/python manage.py migrate --noinput
DJANGO_SUPERUSER_USERNAME=admin DJANGO_SUPERUSER_EMAIL=admin@example.com DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASS" \
  .venv/bin/python manage.py createsuperuser --noinput 2>/dev/null || echo "(el usuario admin ya existía)"

echo "== 5/5 Servicios (arrancan solos) =="
for svc in web listener; do
  if [ "$svc" = web ]; then CMD="manage.py runserver 0.0.0.0:8000 --noreload"; else CMD="manage.py run_mqtt_listener"; fi
  sudo tee /etc/systemd/system/aquacontrol-$svc.service >/dev/null <<EOF
[Unit]
Description=AquaControl $svc
After=network-online.target mosquitto.service

[Service]
User=$ME
WorkingDirectory=$DIR/backend
EnvironmentFile=$DIR/plataforma.env
ExecStart=$DIR/backend/.venv/bin/python $CMD
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
done
sudo systemctl daemon-reload
sudo systemctl enable --now aquacontrol-web aquacontrol-listener

IP=$(hostname -I | awk '{print $1}')
echo
echo "================================================================"
echo " Plataforma lista:  http://$IP:8000/monitoring/"
echo " Usuario: admin    Clave: $ADMIN_PASS"
echo
echo " MQTT  -> broker: $IP (Node-RED en esta Raspberry: 127.0.0.1)"
echo "          usuario: $MQTT_USER   clave: $MQTT_PASS"
echo "================================================================"
.venv/bin/python manage.py seed_demo_data
