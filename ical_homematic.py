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
import numbers
import homematicip
import homematicip.home
import homematicip.device
import homematicip.group
import homematicip.base.enums
from influxdb import InfluxDBClient
from pprint import pprint

# This is where we put the error messages for icinga
error_msg_filename="ical_homematic.msg"

# Our config file
config_files= [ "ical_homematic.ini" ]

# One cycle lasts 60 seconds
cycle_time = 60.

# This is how far we look into the future in hours
lookahead=4

def start_error_log():
    global error_log
    global icinga_status
    error_log=[]
    icinga_status=0

def stop_error_log():
    global error_msg_filename
    global error_log
    global icinga_status
    with open(error_msg_filename,"w") as f:
        f.write(str(icinga_status))
        f.write('\n')
        f.write('\n'.join(error_log))

def error_msg(msg,status=0):
    global error_log
    global icinga_status
    icinga_status=max(icinga_status,status)
    error_log.append(msg)

def logtime():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg,log_level_par=0):
    if log_level_par <= log_level:
        with open("ical_homematic.log","a") as f:
            f.write(f'{logtime()} {msg}\n')

def handle_events(event_list):
    for event in event_list:
        if event["eventType"]==homematicip.base.enums.EventType.GROUP_CHANGED:
            if isinstance(event["data"],homematicip.group.HeatingGroup):
                log(f'EVENT {event["data"].label} boost={event["data"].boostMode}')
        elif event["eventType"]==homematicip.base.enums.EventType.DEVICE_CHANGED:
            if isinstance(event["data"],homematicip.device.HeatingThermostat) or isinstance(event["data"],homematicip.device.HeatingThermostatCompact):
                log(f'EVENT {event["data"].label} valve={event["data"].valvePosition*100.}%')
            elif isinstance(event["data"],homematicip.device.WallMountedThermostatPro):
                log(f'EVENT {event["data"].label} IS {event["data"].actualTemperature}°C SET {event["data"].setPointTemperature}°C')
            elif isinstance(event["data"],homematicip.device.TemperatureHumiditySensorDisplay) or isinstance(event["data"],homematicip.device.TemperatureHumiditySensorWithoutDisplay):
                log(f'EVENT {event["data"].label} IS {event["data"].actualTemperature}°C')
            elif isinstance(event["data"],homematicip.device.PlugableSwitchMeasuring):
                log(f'EVENT {event["data"].label} state={event["data"].on}')

def get_energy_counters():
    global home
    counters=dict()
    for d in home.devices:
        if isinstance(d,homematicip.device.EnergySensorsInterface):
            label=d.label
            counters[label]=dict()
            for subd in d.functionalChannels:
                if isinstance(subd,homematicip.base.functionalChannels.EnergySensorInterfaceChannel):
                    if subd.connectedEnergySensorType == 'ES_GAS':
                        log(f'INFO: {label} gas volume {subd.gasVolume}',0)
                        counters[label]["gas"]=subd.gasVolume
                    elif subd.connectedEnergySensorType == 'ES_IEC':
                        if subd.energyCounterOne:
                            log(f'INFO: {label} energy counter 1 {subd.energyCounterOne}',0)
                            counters[label]["elec1"]=subd.energyCounterOne
                        if subd.energyCounterTwo:
                            log(f'INFO: {label} energy counter 2 {subd.energyCounterTwo}',0)
                            counters[label]["elec2"]=subd.energyCounterTwo
                        if subd.energyCounterThree:
                            log(f'INFO: {label} energy counter 3 {subd.energyCounterThree}',0)
                            counters[label]["elec3"]=subd.energyCounterThree
    return counters

def get_room_data(room):
    global home
    retval=dict()
    retval["thermostats"]=dict()
    retval["switches"]=dict()
    actt=0.0
    num_ht=0
    for g in home.groups:
        if g.groupType=="META" and g.label==room:
            for d in g.devices:
                label=d.label
                if d.lowBat:
                    error_msg(f'Device {label} in room {room} has low battery.',1)
                if d.unreach:
                    error_msg(f'Device {label} in room {room} is not reachable.',2)
                if isinstance(d,homematicip.device.PlugableSwitchMeasuring):
                    retval["switches"][label]={"state": d.on, "energy": d.energyCounter}
                elif isinstance(d,homematicip.device.WallMountedThermostatPro) or isinstance(d,homematicip.device.TemperatureHumiditySensorWithoutDisplay):
                    retval["humidity"]=d.humidity
                    retval["vaporAmount"]=d.vaporAmount
                elif isinstance(d,homematicip.device.HeatingThermostat) or isinstance(d,homematicip.device.HeatingThermostatCompact) or isinstance(d,homematicip.device.HeatingThermostatEvo):
                    vp=d.valvePosition
                    vs=d.valveState
                    actt+=d.valveActualTemperature
                    num_ht+=1
                    if not isinstance (vp,float):
                        error_msg(f'HeatingThermostat {label} in room {room} has valvePosition {vp}.',2)
                    if d.automaticValveAdaptionNeeded:
                        error_msg(f'HeatingThermostat {label} in room {room} requires automatic valve adaption.',2)
                    if vs != "ADAPTION_DONE":
                        error_msg(f'HeatingThermostat {label} in room {room} has valveState {vs}',2)
                    retval["thermostats"][label]=vp
                else:
                    log(f'DEBUG {room}: Unknown device type {type(d).__name__}',1)
        if g.groupType=="HEATING" and g.label==room:
            log(f'DEBUG {room}: This is a HEATING group',1)
            retval["boostDuration"]=g.boostDuration
            retval["setPointTemperature"]=g.setPointTemperature
            retval["actualTemperature"]=g.actualTemperature
    if num_ht >= 1 and not retval["actualTemperature"]:
        log(f'DEBUG {room}: has {num_ht} heating thermostats, but likely no wall-mounted thermostat. We will get the temperature from the average.',1)
        retval["actualTemperature"]=actt/num_ht
    log(f'DEBUG {room}: actualTemperature: {retval["actualTemperature"]}',1)
    return retval

def set_room_temperature(room,temperature):
    global home
    for g in home.groups:
        if g.groupType=="HEATING" and g.label==room:
            g.set_point_temperature(temperature)
            return True
    error_msg(f'Set point temperature could not be set to {temperature} for room {room} because we did not find the proper heating group.',2)
    return False

def set_room_boost(room,status):
    global home
    for g in home.groups:
        if g.groupType=="HEATING" and g.label==room:
            g.set_boost(enable=status)
            return True
    error_msg(f'Boost mode could not be set to {status} for room {room} because we did not find the proper heating group.',2)
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
    error_msg(f'Switch state for switch {switch} in room {room} could not be set to {status} bcuause we did not find the proper device.',2)
    return False

def refresh_calendar(room):
    try:
        ical_string = urllib.request.urlopen(room["url"]).read()
    except:
        error_msg(f'Could not download calendar file for {room}',2)
    else:
        try:
            tmpcal = icalendar.Calendar.from_ical(ical_string)
        except:
            error_msg(f'Could not convert calendar file to icalendar for {room}',2)
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
    for key in [ "high", "low", "lown", "ramp", "night_start", "night_end", "summary_keyword" ]:
        if not key in rooms[room] and key in global_config:
            rooms[room][key]=global_config[key]

log_level=global_config.get("log_level",0)

# Read Homematic IP config
config = homematicip.find_and_load_config_file()
if config == None:
    log("Cannot find config.ini!")
    sys.exit(1)

# Set up Homematic IP and events
print("STEP_1", file=sys.stderr)
home = homematicip.home.Home()
print("STEP_2", file=sys.stderr)
home.set_auth_token(config.auth_token)
print("STEP_3", file=sys.stderr)
home.init(config.access_point)
print("STEP_4", file=sys.stderr)
home.get_current_state()
print("STEP_5", file=sys.stderr)
home.onEvent += handle_events
print("STEP_6", file=sys.stderr)
home.enable_events()
print("STEP_7", file=sys.stderr)

# influxdb for logging
if "influxhost" in global_config:
    try:
        influx=InfluxDBClient(global_config["influxhost"],global_config["influxport"],database=global_config["influxdb"])
    except:
        log(f'Could not setup InfluxDB client. Bye.')
        sys.exit(1)
else:
    influx=None

# Main loop
icinga_status=0
while True:

    start_error_log()

    # Check if the global calendar needs to be refreshed
    if "url" in global_config:
        lu=global_config.get("cal_last_update",datetime.datetime(1970,1,1))
        now=datetime.datetime.now()
        if (now-lu).total_seconds() > 300.:
            log(f'ICAL global: Refreshing ical.')
            refresh_calendar(global_config)

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

    # Local time for lowering of base temperature over night
    start_date_local = datetime.datetime.now()

    energy_counters = get_energy_counters()

    if influx and energy_counters:
        fields={}
        for counter in energy_counters:
            for counter_type in energy_counters[counter]:
                fields[f'counter_{counter}_{counter_type}']=energy_counters[counter][counter_type]
        series=[
                {
                    "measurement": "energy_counters",
                    "tags":        {},
                    "time":        start_date,
                    "fields":      fields
                }
               ]
        try:
            influx.write_points(series)
        except:
            log("Writing energy counters to influxdb failed.")
            error_msg("Writing energy counters to influxdb failed.",1)

    for room in rooms:
        # Get present state of this room
        state=get_room_data(room)

        if influx:
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
            try:
                influx.write_points(series)
            except:
                log("Write to influxdb failed.")
                error_msg("Write to influxdb failed.",1)

        # Stop processing this room in case we only follow it for logging purposes
        if not "thermostats" in state:
            log(f'DEBUG {room}: No thermostats available for this room, continuing with next room after logging.',1)
            continue

        if "url" in rooms[room]:
            # Get events within the next 'lookahead' hours 
            try:
                events = recurring_ical_events.of(rooms[room]["calendar"], skip_bad_series=True).between(start_date, datetime.timedelta(hours=lookahead))
            except:
                log(f'Unable to get events within next {lookahead} hours for room {room}.')
                error_msg(f'Unable to get events within next {lookahead} hours for room {room}.',1)
                events=list()
        elif "url" in global_config:
            # Get events within the next 'lookahead' hours 
            try:
                events = recurring_ical_events.of(global_config["calendar"], skip_bad_series=True).between(start_date, datetime.timedelta(hours=lookahead))
            except:
                log(f'Unable to get events within next {lookahead} hours for global calendar.')
                error_msg(f'Unable to get events within next {lookahead} hours for global calendar.',1)
                events=list()
        else:
            log(f'DEBUG {room}: No calendar available for this room, continuing with next room.',1)
            continue

        heatevents=list()
        if events:
            for event in events:
                myevent=False
                if isinstance(event["DTSTART"].dt,datetime.datetime):
                    if "summary_keyword" in rooms[room]:
                        if rooms[room]["summary_keyword"] in str(event["SUMMARY"]):
                            myevent=event
                    if "ical_resource" in rooms[room] and "RESOURCES" in event:
                        if rooms[room]["ical_resource"] in event["RESOURCES"].split(','):
                            myevent=event
                    if myevent:
                        heatevents.append(myevent)
                        log(f'DEBUG {room}: Event {myevent["SUMMARY"]} (from {myevent["DTSTART"].dt} to {myevent["DTEND"].dt}) ahead!',1)
            heatevents=sorted(heatevents, key=lambda argevent: argevent["DTSTART"].dt)
 
        should_be_in_event=False
        should_be_ramping=False

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
        else:
                should_be_in_event = False
                should_be_ramping = False

        # Flank detection for should_be_in_event
        if should_be_in_event and not rooms[room]["in_event"]:
            log(f'BEGIN {room}: {rooms[room]["event_title"]}')
            if state["setPointTemperature"] + 0.1 < rooms[room]["high"]:
                log(f'ACTION {room}: Setting temperature to {rooms[room]["high"]}°C (Reason: {rooms[room]["event_title"]}).')
                if set_room_temperature(room,rooms[room]["high"]):
                    rooms[room]["in_event"] = True
            else:
                log(f'ACTION {room}: No need to set temperature to {rooms[room]["high"]}°C; it is already at {state["setPointTemperature"]}°C (Reason: {rooms[room]["event_title"]}).')
                rooms[room]["in_event"] = True
            if "heating_switches" in rooms[room]:
                for switch in rooms[room]["heating_switches"]:
                    log(f'ACTION {room}: Setting switch {switch} to on. (Reason: {rooms[room]["event_title"]})')
                    if set_room_switch(room,switch,True):
                        rooms[room]["in_event"] = True
        if not should_be_in_event and rooms[room]["in_event"]:
            log(f'END {room}: {rooms[room]["event_title"]}')
            log(f'ACTION {room}: Setting temperature to {rooms[room]["low"]}°C. (Reason: {rooms[room]["event_title"]})')
            if set_room_temperature(room,rooms[room]["low"]):
                rooms[room]["in_event"] = False
            if "heating_switches" in rooms[room]:
                for switch in rooms[room]["heating_switches"]:
                    log(f'ACTION {room}: Setting switch {switch} to off. (Reason: {rooms[room]["event_title"]})')
                    if set_room_switch(room,switch,False):
                        rooms[room]["in_event"] = False

        # We assume that if heating_switches are not set for this room, boost mode does not make any sense, and neither the pre-heating mode or the overnight reduction
        if "heating_switches" in rooms[room]:
            log(f'DEBUG {room}: This room has heating switches configured. No need to do any ramping, boosting or over-night reduction. Continuing with next room.',1)
            continue

        # In case we are in the ramp-up for the next event, set the set value to "high" when the ramp goes through the current temperature
        if should_be_ramping:
            if isinstance(state["actualTemperature"],numbers.Number):
                if state["actualTemperature"] < rooms[room]["high"] - timetohot * rooms[room]["ramp"]/3600. and state["setPointTemperature"] < rooms[room]["high"]: 
                    log(f'ACTION {room}: Setting temperature to {rooms[room]["high"]}°C at {timetohot} seconds from next event (Reason: {rooms[room]["event_title"]}).')
                    set_room_temperature(room,rooms[room]["high"])

        # Do we need to boost?
        if "boost_threshold" in rooms[room]:
            if isinstance(state["actualTemperature"],numbers.Number):
                if state["setPointTemperature"] - state["actualTemperature"] > rooms[room]["boost_threshold"]:
                    if (start_date - rooms[room]["boostLastSet"]).total_seconds() > state["boostDuration"]*60.0:
                        log(f'ACTION {room}: Setting {state["boostDuration"]} minutes boost mode because set point {state["setPointTemperature"]}°C is more than {threshold}K above the room temperature {state["actualTemperature"]}°C.')
                        set_room_boost(room, True)
                        rooms[room]["boostLastSet"]=start_date

        # Reduction of base temperature over night
        if "night_start" in rooms[room] and "night_end" in rooms[room]:
            log(f'DEBUG {room}: night time reduction configured.',1)
            # Are we in the configured night time period?
            if rooms[room]["night_start"] > rooms[room]["night_end"]:
                log(f'DEBUG {room}: night time reduction period includes midnight.',1)
                # The period with reduced temperature includes midnight. This will be the more common case.
                if start_date_local.hour >= rooms[room]["night_start"] or start_date_local.hour < rooms[room]["night_end"]:
                    should_be_in_night_mode = True
                    log(f'DEBUG {room}: should be in night mode.',1)
                else:
                    should_be_in_night_mode = False
                    log(f'DEBUG {room}: should not be in night mode.',1)
            else:
                log(f'DEBUG {room}: night time reduction period does not include midnight.',1)
                # This is the other case...
                if start_date_local.hour >= rooms[room]["night_start"] and start_date_local.hour < rooms[room]["night_end"]:
                    should_be_in_night_mode = True
                    log(f'DEBUG {room}: should be in night mode.',1)
                else:
                    should_be_in_night_mode = False
                    log(f'DEBUG {room}: should not be in night mode.',1)
            # Flank detection for night mode
            log(f'DEBUG {room}: night_mode: {rooms[room]["night_mode"]}',1)
            if should_be_in_night_mode and not rooms[room]["night_mode"]:
                rooms[room]["night_mode"] = True
                log(f'DEBUG {room} in_event: {rooms[room]["in_event"]} should_be_ramping: {should_be_ramping}',1)
                if not (rooms[room]["in_event"] or should_be_ramping):
                    log(f'ACTION {room}: Setting to reduced base temperature of {rooms[room]["lown"]}°C over night.')
                    if set_room_temperature(room,rooms[room]["lown"]):
                        log(f'ACTION {room}: Entering night mode.')
            elif rooms[room]["night_mode"] and not should_be_in_night_mode:
                rooms[room]["night_mode"] = False
                log(f'DEBUG {room} setPointTemperature: {state["setPointTemperature"]}',1)
                if state["setPointTemperature"] < rooms[room]["low"]:
                    log(f'ACTION {room}: Setting to base temperature of {rooms[room]["low"]}°C.')
                    if set_room_temperature(room,rooms[room]["low"]):
                        log(f'ACTION {room}: Leaving night mode.')
        else:
            log(f'DEBUG {room}: night time reduction NOT configured.',1)

    stop_error_log()

    to_wait = cycle_time - (datetime.datetime.now(datetime.timezone.utc) - start_date).total_seconds()
    if to_wait < 0:
        to_wait = 0.
    time.sleep(to_wait)
