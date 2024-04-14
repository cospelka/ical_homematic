# ical_homematic
Control Homematic IP devices based on a schedule from an iCal calendar

The purpose of this package is to track an ical source, such as a google calendar, and react to events in the calendar to control Homematic IP devices. The initial focus is on heating. There is a direct correspondence between an ical calendar and a Homematic IP room. Events tagged with (HEIZ) in the event title cause the set value temperature in the room to be increased sufficiently in advance such that the target temperature is reached when the event begins. At the end of the event, the set value temperature is returned to the base temperature. In addition, over night, the set value temperature is further reduced. Data is logged to influxdb for monitoring e.g. with grafana. There is also a nagios plugin `check_ical_homematic.py` which lets you check for error messages and that the script is alive. Overview of files:

* `ical_homematic.py` - main file. We assume that this file is placed in `/usr/local/bin`.
* `ical_homematic.service` - systemd unit file to install `ical_homematic.py` as a service. This assumes that we have a unix user `ical_homematic` with home directory `/usr/local/var/ical_homematic` who owns that directory and everything in it. 
* `ical_homematic.ini` - sample config file. Can be installed as `/etc/ical_homematic.ini` or `/usr/local/etc/ical_homematic.ini`.

This package uses https://github.com/hahn-th/homematicip-rest-api to access homematic. The package is likely not included in your linux distribution. To install it, set up a virtual python environment:

```
python -m venv /usr/local/share/mypy
/usr/local/share/mypy/bin/pip install homematicip
```

The main python file assumes the python interpreter from the above virtual environment.

A full installation script (read for background information) that works on debian linux can be found in install_ical_homematic.sh
