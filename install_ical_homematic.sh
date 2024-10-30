#!/bin/bash

if [ -n "$1" ] ; then
	suffix="$1"
	usuffix="_$1"
else
	suffix=""
	usuffix=""
fi	

nagios_plugin_dir="/usr/local/lib/nagios/plugins"
localdir="/var/local/ical_homematic${usuffix}"
localuser="ical_homematic${usuffix}"
hmip_rest_api_confdir="${localdir}/.homematicip-rest-api"
ical_homematic_conffile="${localdir}/ical_homematic.ini"

# Customizing venv not supported, as the path is hardcoded in the first line of ical_homematic.py.
venv="/usr/local/share/mypy"

# Customization not supported, as the path is hardcoded in the systemd unit file ical_homematic.service.
bindir="/usr/local/bin"

# Do not change unless you know what you are doing.
srcdir="/usr/local/src/ical_homematic"

echo "Installation erforderlicher debian Pakete."
apt install python3-influxdb python3-pip python3-venv

if [ -d "${venv}" ] ; then
  echo "Virtuelle python Umgebung ${venv} existiert bereits."
else
  echo "Erstellen von virtueller python Umgebung ${venv}."
  python -m venv "${mypy}"
fi

echo "Installation erforderlicher python Pakete mit pip in ${venv}."
"${venv}/bin/pip" install homematicip

if [ -d "${localdir}" ] ; then
  echo "Verzeichnis ${localdir} für Logs und Statusdateien sowie als Homeverzeichnis für ${localuser} existiert bereits."
else
  echo "Erstellen von Verzeichnis ${localdir} für Logs und Statusdateien sowie als Homeverzeichnis für ${localuser}."
  mkdir -p "${localdir}"
fi

if id "${localuser}" >/dev/null 2>&1 ; then
  echo "Benutzer ${localuser} für den ical_homematic Systemdienst existiert bereits."
else
  echo "Erstellen eines Benutzers ${localuser} für den ical_homematic Systemdienst."
  adduser --system --comment "System user for ical_homematic service" --group --home "${localdir}" --no-create-home "${localuser}"
fi

chown "${localuser}:" "${localdir}"
chmod 755 "${localdir}"

echo "Clonen der Quellen von ical_homematic per git."
rm -rf "${srcdir}"
git clone https://github.com/cospelka/ical_homematic.git "${srcdir}"
cd "${srcdir}"

if [ -d "${hmip_rest_api_confdir}" ] ; then
  echo "Verzeichnis ${hmip_rest_api_confdir} existiert bereits."
else
  echo "Erzeugen des Verzeichnisses ${hmip_rest_api_confdir}."
  mkdir -p "${hmip_rest_api_confdir}"
  chown "${localuser}:" "${hmip_rest_api_confdir}"
fi

if [ -f "${hmip_rest_api_confdir}/config.ini" ] ; then
  echo "Konfigurationsdatei ${hmip_rest_api_confdir}/config.ini für homematic ip REST API existiert bereits."
else
  echo "Verbindung mit homematic ip Installation über homematic ip REST API herstellen und Konfigurationsdatei erzeugen."
  "${venv}/bin/hmip_generate_auth_token"
  echo "Ablegen der Konfigurationsdatei in ${hmip_rest_api_confdir}/config.ini"
  mv config.ini "${hmip_rest_api_confdir}/"
  chown "${localuser}:" "${hmip_rest_api_confdir}/config.ini"
  chmod 600 "${hmip_rest_api_confdir}/config.ini"
fi

if ! [ -f "${hmip_rest_api_confdir}/config.ini" ] ; then
  echo "${hmip_rest_api_confdir}/config.ini konnte nicht erzeugt werden. Tschüs."
  exit 1
fi

echo "Installiere ical_homematic.py in /usr/local/bin"
cp "${srcdir}/ical_homematic.py" "${bindir}"
chmod 755 "${bindir}/ical_homematic.py"

echo "Installiere nagios/icinga plugin in ${nagios_plugin_dir}."
mkdir -p "${nagios_plugin_dir}"
cp "${srcdir}/check_ical_homematic.py" "${nagios_plugin_dir}/"

if [ -f "$ical_homematic_conffile" ] ; then
  echo "Konfigurationsdatei ${ical_homematic_conffile} existiert bereits und wird nicht überschrieben."
else
  echo "Erstelle Konfigurationsdatei ${ical_homematic_conffile}."
  if [ -f "/root/ical_homematic_local/ical_homematic.ini" ] ; then
    cp "/root/ical_homematic_local/ical_homematic.ini" "$ical_homematic_conffile" 
  else
    cp "${srcdir}/ical_homematic.ini" "$ical_homematic_conffile"
  fi
  chown "${localuser}:" "${ical_homematic_conffile}"
  chmod 600 "${ical_homematic_conffile}"
fi

echo "Erstelle Systemdienst für ical_homematic."
servicefile=/etc/systemd/system/ical_homematic${usuffix}.service
cp "${srcdir}/ical_homematic.service" "$servicefile"
sed -i -e "s/^User=ical_homematic/User=${localuser}/"  "$servicefile"
sed -i -e "s/^Group=ical_homematic/Group=${localuser}/" "$servicefile"
sed -i -e "s/^WorkingDirectory=\/var\/local\/ical_homematic/WorkingDirectory=\/var\/local\/ical_homematic${usuffix}/" "$servicefile"

systemctl daemon-reload
systemctl enable --now ical_homematic${usuffix}.service
