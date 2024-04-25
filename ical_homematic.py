#!/usr/local/share/mypy/bin/python3
# Copyright (C) 2024 Christian Ospelkaus
# This file is part of ical_homematic <https://github.com/cospelka/ical_homematic>.
#
# ical_homematic is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ical_homematic is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ical_homematic.  If not, see <http://www.gnu.org/licenses/>.


import os
import sys
import icalendar
import recurring_ical_events
import urllib.request
import datetime
import json
import time
import configparser
import homematicip
import homematicip.home
import homematicip.device
import homematicip.group
import homematicip.base.enums
from influxdb import InfluxDBClient

# This is where we put the error messages for icinga
error_msg_filename="/usr/local/var/ical_homematic/ical_homematic.msg"
error_msg_filename_tmp=error_msg_filename + ".tmp"

# Our config file
config_files= [ "/usr/local/etc/ical_homematic.ini", "/etc/ical_homematic.ini" ]

# One cycle lasts 60 seconds
cycle_time = 60.

# This is how far we look into the future in hours
lookahead=4

# keyword to look for in the SUMMARY field of an event; if the keyword 
# is not present, we ignore the event. 
heating_keyword="(HEIZ)"

def start_error_log():
    global error_msg_filename_tmp
    with open(error_msg_filename_tmp,"w") as f:
        f.write('')

def stop_error_log():
    global error_msg_filename
    global error_msg_filename_tmp
    os.rename(error_msg_filename_tmp,error_msg_filename)

def error_msg(msg):
    global error_msg_filename
    with open(error_msg_filename,"a") as f:
        f.write(f'{msg}\n')

def logtime():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg):
    with open("/usr/local/var/ical_homematic/ical_homematic.log","a") as f:
        f.write(f'{logtime()} {msg}\n')

def handle_events(event_list):
    for event in event_list:
        if event["eventType"]==homematicip.base.enums.EventType.GROUP_CHANGED:
            if isinstance(event["data"],homematicip.group.HeatingGroup):
                log(f'EVENT {event["data"].label} boost={event["data"].boostMode}')
        elif event["eventType"]==homematicip.base.enums.EventType.DEVICE_CHANGED:
            if isinstance(event["data"],homematicip.device.HeatingThermostat) or isinstance(event["data"],homematicip.device.HeatingThermostatCompact):
                log(f'EVENT {event["data"].label} valve={event["data"].valvePosition*100.}%')
            elif isinstance(event["data"],homematicip.device.TemperatureHumiditySensorDisplay) or isinstance(event["data"],homematicip.device.TemperatureHumiditySensorWithoutDisplay):
                log(f'EVENT {event["data"].label} IS {event["data"].actualTemperature}°C')
            elif isinstance(event["data"],homematicip.device.WallMountedThermostatPro):
                log(f'EVENT {event["data"].label} IS {event["data"].actualTemperature}°C SET {event["data"].setPointTemperature}°C')
            elif isinstance(event["data"],homematicip.device.PlugableSwitchMeasuring):
                log(f'EVENT {event["data"].label} state={event["data"].on}')

def get_room_data(room):
    global home
    retval=dict()
    retval["thermostats"]=dict()
    retval["switches"]=dict()
    for g in home.groups:
        if g.groupType=="META" and g.label==room:
            for d in g.devices:
                label=d.label
                if d.lowBat:
                    error_msg(f'{logtime()} Device {label} in room {room} has low battery.')
                if d.unreach:
                    error_msg(f'{logtime()} Device {label} in room {room} is not reachable.')
                if isinstance(d,homematicip.device.PlugableSwitchMeasuring):
                    retval["switches"][label]={"state": d.on, "energy": d.energyCounter}
                if isinstance(d,homematicip.device.WallMountedThermostatPro) or isinstance(d,homematicip.device.TemperatureHumiditySensorWithoutDisplay):
                    retval["humidity"]=d.humidity
                    retval["vaporAmount"]=d.vaporAmount
                elif isinstance(d,homematicip.device.HeatingThermostat) or isinstance(d,homematicip.device.HeatingThermostatCompact):
                    vp=d.valvePosition
                    vs=d.valveState
                    if not isinstance (vp,float):
                        error_msg(f'{logtime()} HeatingThermostat {label} in room {room} has valvePosition {vp}.')
                    if d.automaticValveAdaptionNeeded:
                        error_msg(f'{logtime()} HeatingThermostat {label} in room {room} requires automatic valve adaption.')
                    if vs != "ADAPTION_DONE":
                        error_msg(f'{logtime()} HeatingThermostat {label} in room {room} has valveState {vs}')
                    retval["thermostats"][label]=vp
        if g.groupType=="HEATING" and g.label==room:
            retval["boostDuration"]=g.boostDuration
            retval["setPointTemperature"]=g.setPointTemperature
            retval["actualTemperature"]=g.actualTemperature
    return retval

def set_room_temperature(room,temperature):
    global home
    for g in home.groups:
        if g.groupType=="HEATING" and g.label==room:
            g.set_point_temperature(temperature)
            return True
    error_msg(f'{logtime()} Set point temperature could not be set to {temperature} for room {room} because we did not find the proper heating group.')
    return False

def set_room_boost(room,status):
    global home
    for g in home.groups:
        if g.groupType=="HEATING" and g.label==room:
            g.set_boost(enable=status)
            return True
    error_msg(f'{logtime()} Boost mode could not be set to {status} for room {room} because we did not find the proper heating group.')
    return False

def set_room_switch(room,switch,status):
    global home
    for g in home.groups:
        if g.groupType=="META" and g.label==room:
            for d in g.devices:
                label=d.label
                if isinstance(d,homematicip.device.PlugableSwitchMeasuring) and label==switch:
                    d.set_switch_state(status)
                    return True
    error_msg(f'{logtime()} Switch state for switch {switch} in room {room} could not be set to {status} bcuause we did not find the proper device.')
    return False

def refresh_calendar(room):
    try:
        ical_string = urllib.request.urlopen(room["url"]).read()
    except:
        error_msg(f'Could not download calendar file for {room}')
    else:
        try:
            tmpcal = icalendar.Calendar.from_ical(ical_string)
        except:
            error_msg(f'{logtime()} Could not convert calendar file to icalendar for {room}')
        else:
            room["calendar"] = tmpcal
            room["cal_last_update"] = datetime.datetime.now()

# Read our own config file
iniparser = configparser.RawConfigParser()
iniparser.optionxform=str
rooms = dict()
rooms["global"]=dict()
rooms["global"]["high"]=21.0
rooms["global"]["low"]=18.0
rooms["global"]["lown"]=16.0
rooms["global"]["ramp"]=1.0
rooms["global"]["influxdb"]="ical_homematic"
rooms["global"]["influxhost"]="localhost"
rooms["global"]["influxport"]=8086

for config_file in config_files:
    try:
        iniparser.read(config_file)
    except:
        continue
    for room in iniparser.sections():
        if not room in rooms:
            rooms[room]=dict()
        for key in iniparser[room]:
            try:
                rooms[room][key] = json.loads(iniparser[room][key])
            except Exception as e:
                log(e)
                log(f'JSON parse error in section {section}, key {key}. Bye.')
                sys.exit(1)

# Initialize some status variables and pick up globals
if "global" in rooms:
    global_config=rooms.pop("global")
else:
    global_config=dict()
for room in rooms:
    rooms[room]["in_event"] =  False
    rooms[room]["boostLastSet"] = datetime.datetime(1970,1,1,tzinfo=datetime.timezone.utc)
    rooms[room]["night_mode"] = False
    for key in [ "high", "low", "lown", "ramp", "night_start", "night_end" ]:
        if not key in rooms[room] and key in global_config:
            rooms[room][key]=global_config[key]

# Read Homematic IP config
config = homematicip.find_and_load_config_file()
if config == None:
    log("Cannot find config.ini!")
    sys.exit(1)

# Set up Homematic IP and events
home = homematicip.home.Home()
home.set_auth_token(config.auth_token)
home.init(config.access_point)
home.get_current_state()
home.onEvent += handle_events
home.enable_events()

# influxdb for logging
try:
    influx=InfluxDBClient(global_config["influxhost"],global_config["influxport"],database=global_config["influxdb"])
except:
    log(f'Could not setup InfluxDB client. Bye.')
    sys.exit(1)

# Main loop
while True:

    start_error_log()

    # Check if any of the calendars need to be refreshed
    for room in rooms:
        if "url" in rooms[room]:
            lu=rooms[room].get("cal_last_update",datetime.datetime(1970,1,1))
            now=datetime.datetime.now()
            if (now-lu).total_seconds() > 300.:
                log(f'ICAL {room}: Refreshing ical.')
                refresh_calendar(rooms[room])

    # UTC for interaction with online calendar
    start_date = datetime.datetime.now(datetime.timezone.utc)
    end_date = start_date + datetime.timedelta(hours=lookahead)

    # Local time for lowering of base temperature over night
    start_date_local = datetime.datetime.now()

    for room in rooms:
        # Get present state of this room
        state=get_room_data(room)

        fields={}
        for thermostat in state["thermostats"]:
            fields[thermostat+".valvePosition"]=state["thermostats"][thermostat]
        for fn in [ "actualTemperature", "setPointTemperature", "humidity", "vaporAmount" ]:
            if fn in state:
                fields[fn]=state[fn]
        series=[
                {
                    "measurement": rooms[room]["influx_name"],
                    "tags":        {},
                    "time":        start_date,
                    "fields":      fields
                }
               ]
        influx.write_points(series)

        # Stop processing this room in case we only follow it for logging purposes
        if not "url" in rooms[room]:
            continue

        # Get events within the next 'lookahead' hours 
        events = sorted(recurring_ical_events.of(rooms[room]["calendar"]).between(start_date, end_date), key=lambda event: event["DTSTART"].dt)

        should_be_in_event=False
        should_be_ramping=False

        # Are there any events within the next 'lookahead' hours that contain heating_keyword?
        heatevents=list()
        for event in events:
            if heating_keyword in str(events[0]["SUMMARY"]):
                heatevents.append(event)

        if heatevents:
            # Time to event
            timetohot=(heatevents[0]["DTSTART"].dt - start_date).total_seconds()
            rooms[room]["event_title"] = str(heatevents[0]["SUMMARY"])
            if timetohot < 0: 
                # We are already in the event
                should_be_in_event = True
                if len(heatevents) >= 2:
                    # We are in the event and the next event is already in sight
                    should_be_ramping = True
                    timetohot=(heatevents[1]["DTSTART"].dt - start_date).total_seconds()
                else:
                    # We are in the event and no other event is in sight within the next 'lookahead' hours
                    should_be_ramping = False
            else:
                # We are not in the event yet
                should_be_in_event = False
                should_be_ramping = True

        # Flank detection for should_be_in_event
        if should_be_in_event and not rooms[room]["in_event"]:
            rooms[room]["in_event"] = True
            log(f'BEGIN {room}: {rooms[room]["event_title"]}')
            if state["setPointTemperature"] + 0.1 < rooms[room]["high"]:
                log(f'ACTION {room}: Setting temperature to {rooms[room]["high"]}°C (Reason: {rooms[room]["event_title"]}).')
                set_room_temperature(room,rooms[room]["high"])
            else:
                log(f'ACTION {room}: No need to set temperature to {rooms[room]["high"]}°C; it is already at {state["setPointTemperature"]}°C (Reason: {rooms[room]["event_title"]}).')
            if "heating_switches" in rooms[room]:
                for switch in rooms[room]["heating_switches"]:
                    log(f'ACTION {room}: Setting switch {switch} to on. (Reason: {rooms[room]["event_title"]})')
                    set_room_switch(room,switch,True)
        if not should_be_in_event and rooms[room]["in_event"]:
            rooms[room]["in_event"] = False
            log(f'END {room}: {rooms[room]["event_title"]}')
            log(f'ACTION {room}: Setting temperature to {rooms[room]["low"]}°C. (Reason: {rooms[room]["event_title"]})')
            set_room_temperature(room,rooms[room]["low"])
            if "heating_switches" in rooms[room]:
                for switch in rooms[room]["heating_switches"]:
                    log(f'ACTION {room}: Setting switch {switch} to off. (Reason: {rooms[room]["event_title"]})')
                    set_room_switch(room,switch,False)

        # We assume that if heating_switches are not set for this room, boost mode does not make any sense, and neither the pre-heating mode or the overnight reduction
        if "heating_switches" in rooms[room]:
            continue

        # In case we are in the ramp-up for the next event, set the set value to "high" when the ramp goes through the current temperature
        if should_be_ramping:
            if state["actualTemperature"] < rooms[room]["high"] - timetohot * rooms[room]["ramp"]/3600. and state["setPointTemperature"] < rooms[room]["high"]: 
                log(f'ACTION {room}: Setting temperature to {rooms[room]["high"]}°C at {timetohot} seconds from next event (Reason: {rooms[room]["event_title"]}).')
                set_room_temperature(room,rooms[room]["high"])

        # Do we need to boost?
        if "boost_threshold" in rooms[room]:
            if state["setPointTemperature"] - state["actualTemperature"] > rooms[room]["boost_threshold"]:
                if (start_date - rooms[room]["boostLastSet"]).total_seconds() > state["boostDuration"]*60.0:
                    log(f'ACTION {room}: Setting {state["boostDuration"]} minutes boost mode because set point {state["setPointTemperature"]}°C is more than {threshold}K above the room temperature {state["actualTemperature"]}°C.')
                    set_room_boost(room, True)
                    rooms[room]["boostLastSet"]=start_date

        # Reduction of base temperature over night
        if "night_start" in rooms[room] and "night_end" in rooms[room]:
            # Are we in the configured night time period?
            if rooms[room]["night_start"] > rooms[room]["night_end"]:
                # The period with reduced temperature includes midnight. This will be the more common case.
                if start_date_local.hour >= rooms[room]["night_start"] or start_date_local.hour < rooms[room]["night_end"]:
                    should_be_in_night_mode = True
                else:
                    should_be_in_night_mode = False
            else:
                # This is the other case...
                if start_date_local.hour >= rooms[room]["night_start"] and start_date_local.hour < rooms[room]["night_end"]:
                    should_be_in_night_mode = True
                else:
                    should_be_in_night_mode = False
            # Flank detection for night mode
            if should_be_in_night_mode and not rooms[room]["night_mode"]:
                if not (rooms[room]["in_event"] or should_be_ramping):
                    log(f'ACTION {room}: Setting to reduced base temperature of {rooms[room]["lown"]}°C over night.')
                    set_room_temperature(room,rooms[room]["lown"])
                    rooms[room]["night_mode"] = True
            elif rooms[room]["night_mode"] and not should_be_in_night_mode:
                if state["setPointTemperature"] < rooms[room]["low"]:
                    log(f'ACTION {room}: Setting to base temperature of {rooms[room]["low"]}°C.')
                    set_room_temperature(room,rooms[room]["low"])
                log(f'ACTION {room}: Leaving night mode.')
                rooms[room]["night_mode"] = False

    stop_error_log()

    to_wait = cycle_time - (datetime.datetime.now(datetime.timezone.utc) - start_date).total_seconds()
    if to_wait < 0:
        to_wait = 0.
    time.sleep(to_wait)
