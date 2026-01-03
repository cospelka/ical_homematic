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
import asyncio
import homematicip
import homematicip.home
import homematicip.device
import homematicip.group
import homematicip.base.enums
from systemd.daemon import notify
from influxdb import InfluxDBClient
from pprint import pprint

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
        notify("WATCHDOG=1")
        if event["eventType"]==homematicip.base.enums.EventType.GROUP_CHANGED:
            if isinstance(event["data"],homematicip.group.HeatingGroup):
                log(f'EVENT {event["data"].label} boost={event["data"].boostMode}')
        elif event["eventType"]==homematicip.base.enums.EventType.DEVICE_CHANGED:
            if isinstance(event["data"],homematicip.device.HeatingThermostat) or isinstance(event["data"],homematicip.device.HeatingThermostatCompact):
                vp=event["data"].valvePosition
                if type(vp) == int or type(vp) == float:
                    vp *= 100.
                    vp = f'{vp:.1f} %'
                log(f'EVENT {event["data"].label} valve={vp}')
            elif isinstance(event["data"],homematicip.device.WallMountedThermostatPro):
                log(f'EVENT {event["data"].label} IS {event["data"].actualTemperature}°C SET {event["data"].setPointTemperature}°C')
            elif isinstance(event["data"],homematicip.device.TemperatureHumiditySensorDisplay) or isinstance(event["data"],homematicip.device.TemperatureHumiditySensorWithoutDisplay):
                log(f'EVENT {event["data"].label} IS {event["data"].actualTemperature}°C')
            elif isinstance(event["data"],homematicip.device.SwitchMeasuring):
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

def get_rooms():
    global home
    global rooms
    for g in home.groups:
        if g.groupType=="HEATING":
            if not g.label in rooms:
                rooms[g.label]={}

def get_room_data(roomname):
    global home
    global rooms
    retval=dict()
    retval["thermostats"]=dict()
    retval["switches"]=dict()
    actt=0.0
    num_ht=0
    for g in home.groups:
        if g.groupType=="META" and g.label==roomname:
            for d in g.devices:
                label=d.label
                if d.lowBat:
                    error_msg(f'Device {label} in room {roomname} has low battery.',1)
                if d.unreach:
                    error_msg(f'Device {label} in room {roomname} is not reachable.',2)
                if isinstance(d,homematicip.device.SwitchMeasuring):
                    retval["switches"][label]={"state": d.on, "energy": d.energyCounter}
                elif isinstance(d,homematicip.device.WallMountedThermostatPro) or isinstance(d,homematicip.device.TemperatureHumiditySensorWithoutDisplay):
                    retval["humidity"]=d.humidity
                    retval["vaporAmount"]=d.vaporAmount
                elif isinstance(d,homematicip.device.HeatingThermostat) or isinstance(d,homematicip.device.HeatingThermostatCompact) or isinstance(d,homematicip.device.HeatingThermostatEvo):
                    vp=d.valvePosition
                    vs=d.valveState
                    if isinstance(d.valveActualTemperature,numbers.Number): 
                        actt+=d.valveActualTemperature
                        num_ht+=1
                    if not isinstance (vp,float):
                        error_msg(f'HeatingThermostat {label} in room {roomname} has valvePosition {vp}.',1)
                    if d.automaticValveAdaptionNeeded:
                        error_msg(f'HeatingThermostat {label} in room {roomname} requires automatic valve adaption.',2)
                    if vs != "ADAPTION_DONE":
                        error_msg(f'HeatingThermostat {label} in room {roomname} has valveState {vs}',1)
                    retval["thermostats"][label]=vp
                else:
                    log(f'DEBUG {roomname}: Unknown device type {type(d).__name__}',1)
        if g.groupType=="HEATING":
            # log(f'DEBUG {g.label}: has control mode {g.controlMode}',1)
            if g.label==roomname:
                log(f'DEBUG {roomname}: This is a HEATING group',1)
                retval["boostDuration"]=g.boostDuration
                retval["setPointTemperature"]=g.setPointTemperature
                retval["actualTemperature"]=g.actualTemperature
                retval["controlMode"]=g.controlMode
                if "url" in rooms[g.label] or "ical_resource" in rooms[g.label]:
                    if not g.controlMode == 'MANUAL' and not g.controlMode == 'ECO':
                        log(f'DEBUG {g.label}: Setting controlMode to MANUAL',1)
                        try:
                            g.set_control_mode('MANUAL')
                        except:
                            log(f'ERROR {g.label}: Setting controlMode to MANUAL failed.',1)

            if ( not g.label in rooms ) or ( g.label in rooms and ( not ("url" in rooms[g.label] or "ical_resource" in rooms[g.label] ))):
                if not g.controlMode == 'AUTOMATIC' and not g.controlMode == 'ECO':
                    log(f'DEBUG {g.label}: Setting controlMode to AUTOMATIC',1)
                    try:
                        g.set_control_mode('AUTOMATIC')
                    except:
                        log(f'ERROR {g.label}: Setting controlMode to AUTOMATIC failed.',1)
    if num_ht >= 1 and not retval["actualTemperature"]:
        log(f'DEBUG {roomname}: has {num_ht} heating thermostats, but likely no wall-mounted thermostat. We will get the temperature from the average.',1)
        retval["actualTemperature"]=actt/num_ht
    log(f'DEBUG {roomname}: actualTemperature: {retval["actualTemperature"]}',1)
    return retval

async def set_room_temperature(roomname,temperature):
    global home
    for g in home.groups:
        if g.groupType=="HEATING" and g.label==roomname:
            if not g.controlMode == 'ECO':
                await g.set_point_temperature_async(temperature)
            return True
    error_msg(f'Set point temperature could not be set to {temperature} for room {roomname} because we did not find the proper heating group.',2)
    return False

async def set_room_boost(roomname,status):
    global home
    for g in home.groups:
        if g.groupType=="HEATING" and g.label==roomname:
            await g.set_boost_async(enable=status)
            return True
    error_msg(f'Boost mode could not be set to {status} for room {roomname} because we did not find the proper heating group.',2)
    return False

async def set_room_switch(roomname,switch,status):
    global home
    for g in home.groups:
        if g.groupType=="META" and g.label==roomname:
            for d in g.devices:
                label=d.label
                if isinstance(d,homematicip.device.SwitchMeasuring) and label==switch:
                    await d.set_switch_state_async(status)
                    return True
    error_msg(f'Switch state for switch {switch} in room {roomname} could not be set to {status} bcuause we did not find the proper device.',2)
    return False

def refresh_calendar(room,label):
    try:
        ical_string = urllib.request.urlopen(room["url"]).read()
    except:
        error_msg(f'Could not download calendar file for {label}',2)
    else:
        try:
            tmpcal = icalendar.Calendar.from_ical(ical_string)
        except:
            error_msg(f'Could not convert calendar file to icalendar for {label}',2)
        else:
            room["calendar"] = tmpcal
            room["cal_last_update"] = datetime.datetime.now()

async def main_loop():
    # Main loop
    global home
    global rooms
    global global_config
    global influx
    home.onEvent += handle_events
    await home.enable_events()

    icinga_status=0
    while True:

        start_error_log()

        # Check if the global calendar needs to be refreshed
        if "url" in global_config:
            lu=global_config.get("cal_last_update",datetime.datetime(1970,1,1))
            now=datetime.datetime.now()
            if (now-lu).total_seconds() > 300.:
                log(f'ICAL global: Refreshing ical.')
                refresh_calendar(global_config,"global calendar")

        # Check if any of the calendars need to be refreshed
        for room in rooms:
            if "url" in rooms[room]:
                lu=rooms[room].get("cal_last_update",datetime.datetime(1970,1,1))
                now=datetime.datetime.now()
                if (now-lu).total_seconds() > 300.:
                    log(f'ICAL {room}: Refreshing ical.')
                    refresh_calendar(rooms[room],room)

        # UTC for interaction with online calendar
        start_date = datetime.datetime.now(datetime.timezone.utc)
    
        # Local time for lowering of base temperature over night
        start_date_local = datetime.datetime.now()

        energy_counters = get_energy_counters()

        if influx and energy_counters:
            series=[]
            for counter in energy_counters:
                for counter_type in energy_counters[counter]:
                    if counter_type == "gas":
                        series.append({
                                        "measurement": "energy",
                                        "tags":        { "type": "gas", "name": counter },
                                        "time":        start_date,
                                        "fields":      { "gas": energy_counters[counter][counter_type] }
                                      })
                    elif counter_type.startswith("elec"):
                        series.append({
                                        "measurement": "energy",
                                        "tags":        { "type": "electrical", "name": counter, "number": counter_type.removeprefix("elec") },
                                        "time":        start_date,
                                        "fields":      { "electricity": energy_counters[counter][counter_type] }
                                      })
            if series:
                try:
                    influx.write_points(series)
                except:
                    log("Writing energy counters to influxdb failed.")
                    error_msg("Writing energy counters to influxdb failed.",1)

        for room in rooms:
            # Get present state of this room
            state=get_room_data(room)

            if influx:
                series=[]
                fields={}
                for fn in [ "actualTemperature", "setPointTemperature", "humidity", "vaporAmount" ]:
                    if fn in state and isinstance(state[fn],numbers.Number): 
                        fields[fn]=float(state[fn])
                if not state["thermostats"]:
                    if "setPointTemperature" in fields:
                        fields.pop("setPointTemperature")
                if fields:
                    series.append({
                                "measurement": "homematic_rooms",
                                "tags":        { "room": rooms[room]["influx_name"], "subroom": rooms[room]["subroom"] },
                                "fields":     fields,
                                "time":        start_date
                                })

                for thermostat in state["thermostats"]:
                    if isinstance(state["thermostats"][thermostat],numbers.Number): 
                        series.append({
                                    "measurement": "homematic_rooms",
                                    "tags":        { "room": rooms[room]["influx_name"], "subroom": rooms[room]["subroom"], "thermostat": thermostat },
                                    "fields":      { "vp": state["thermostats"][thermostat] },
                                    "time":        start_date
                                    })
                if series:
                    try:
                        influx.write_points(series)
                    except Exception as e:
                        log(f'Write to influxdb failed for room {room} with subroom {rooms[room]["subroom"]} on measurement {rooms[room]["influx_name"]}.')
                        log(f'Message was {e}.')
                        log(f'Data was {series}.')
                        error_msg("Write to influxdb failed.",1)

            # Stop processing this room in case we only follow it for logging purposes
            if not "thermostats" in state:
                log(f'DEBUG {room}: No thermostats available for this room, continuing with next room after logging.',1)
                continue

            if "url" in rooms[room] and "calendar" in rooms[room]:
                calendar_source=rooms[room]["calendar"]
            elif "ical_resource" in rooms[room] and "url" in global_config and "calendar" in global_config:
                calendar_source=global_config["calendar"]
            else:
                log(f'DEBUG {room}: No calendar available for this room, continuing with next room.',1)
                continue

            try:
                events = recurring_ical_events.of(calendar_source, skip_bad_series=True).between(start_date, datetime.timedelta(hours=lookahead))
            except:
                log(f'Unable to get events within next {lookahead} hours for room {room}.')
                error_msg(f'Unable to get events within next {lookahead} hours for room {room}.',1)
                events=list()

            heatevents=list()
            if events:
                for event in events:
                    myevent=False
                    if isinstance(event["DTSTART"].dt,datetime.datetime):
                        if "summary_keyword" in rooms[room]:
                            if rooms[room]["summary_keyword"] in str(event["SUMMARY"]):
                                myevent=event
                        if "ical_resource" in rooms[room] and "RESOURCES" in event:
                            resources=event["RESOURCES"].split(',')
                            resources=[element.strip() for element in resources]
                            log(f'DEBUG {room}: Event {event["SUMMARY"]} has resources {event["RESOURCES"]}')
                            if rooms[room]["ical_resource"] in resources:
                                if not ( rooms[room]["veto_resource"] != "" and rooms[room]["veto_resource"] in resources ):
                                    myevent=event
                                else:
                                    log(f'DEBUG {room}: Event {event["SUMMARY"]} (from {event["DTSTART"].dt} to {event["DTEND"].dt}) skipped because of veto resource',1)
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
                if isinstance(state["setPointTemperature"],numbers.Number):
                    if state["setPointTemperature"] + 0.1 < rooms[room]["high"]:
                        log(f'ACTION {room}: Setting temperature to {rooms[room]["high"]}°C (Reason: {rooms[room]["event_title"]}).')
                        if await set_room_temperature(room,rooms[room]["high"]):
                            rooms[room]["in_event"] = True
                    else:
                        log(f'ACTION {room}: No need to set temperature to {rooms[room]["high"]}°C; it is already at {state["setPointTemperature"]}°C (Reason: {rooms[room]["event_title"]}).')
                        rooms[room]["in_event"] = True
                    if "heating_switches" in rooms[room]:
                        for switch in rooms[room]["heating_switches"]:
                            log(f'ACTION {room}: Setting switch {switch} to on. (Reason: {rooms[room]["event_title"]})')
                            if await set_room_switch(room,switch,True):
                                rooms[room]["in_event"] = True
                else:
                    log(f'WARNING {room}: setPointTemperature is not numeric!')
            if not should_be_in_event and rooms[room]["in_event"]:
                log(f'END {room}: {rooms[room]["event_title"]}')
                log(f'ACTION {room}: Setting temperature to {rooms[room]["low"]}°C. (Reason: {rooms[room]["event_title"]})')
                if await set_room_temperature(room,rooms[room]["low"]):
                    rooms[room]["in_event"] = False
                if "heating_switches" in rooms[room]:
                    for switch in rooms[room]["heating_switches"]:
                        log(f'ACTION {room}: Setting switch {switch} to off. (Reason: {rooms[room]["event_title"]})')
                        if await set_room_switch(room,switch,False):
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
                        await set_room_temperature(room,rooms[room]["high"])

            # Do we need to boost?
            if "boost_threshold" in rooms[room]:
                if isinstance(state["actualTemperature"],numbers.Number):
                    if state["setPointTemperature"] - state["actualTemperature"] > rooms[room]["boost_threshold"]:
                        if (start_date - rooms[room]["boostLastSet"]).total_seconds() > state["boostDuration"]*60.0:
                            log(f'ACTION {room}: Setting {state["boostDuration"]} minutes boost mode because set point {state["setPointTemperature"]}°C is more than {threshold}K above the room temperature {state["actualTemperature"]}°C.')
                            await set_room_boost(room, True)
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
                        if await set_room_temperature(room,rooms[room]["lown"]):
                            log(f'ACTION {room}: Entering night mode.')
                elif rooms[room]["night_mode"] and not should_be_in_night_mode:
                    rooms[room]["night_mode"] = False
                    log(f'DEBUG {room} setPointTemperature: {state["setPointTemperature"]}',1)
                    if state["setPointTemperature"] < rooms[room]["low"]:
                        log(f'ACTION {room}: Setting to base temperature of {rooms[room]["low"]}°C.')
                        if await set_room_temperature(room,rooms[room]["low"]):
                            log(f'ACTION {room}: Leaving night mode.')
            else:
                log(f'DEBUG {room}: night time reduction NOT configured.',1)

        stop_error_log()

        to_wait = cycle_time - (datetime.datetime.now(datetime.timezone.utc) - start_date).total_seconds()
        if to_wait < 0:
            to_wait = 0.
        log(f'INFO: sleeping for {to_wait} s.')
        await asyncio.sleep(to_wait)


if __name__ == "__main__":

    # This is where we put the error messages for icinga
    error_msg_filename="ical_homematic.msg"

    # Our config file
    config_files= [ "ical_homematic.ini" ]

    # One cycle lasts 60 seconds
    cycle_time = 60.

    # This is how far we look into the future in hours
    lookahead=4

    # Read our own config file
    iniparser = configparser.RawConfigParser()
    iniparser.optionxform=str
    rooms = dict()

    inisections={}
    inisections["global"]=dict()
    inisections["global"]["high"]=21.0
    inisections["global"]["low"]=18.0
    inisections["global"]["lown"]=16.0
    inisections["global"]["ramp"]=1.0
    inisections["global"]["veto_resource"]=""
    for config_file in config_files:
        try:
            iniparser.read(config_file)
        except:
            continue
        for section_name in iniparser.sections():
            if not section_name in inisections:
                inisections[section_name]=dict()
            for key in iniparser[section_name]:
                try:
                    inisections[section_name][key] = json.loads(iniparser[section_name][key])
                except Exception as e:
                    log(e)
                    log(f'JSON parse error in section {section_name}, key {key}. Bye.')
                    sys.exit(1)

    global_config=inisections.pop("global")
    log_level=global_config.get("log_level",0)

    config = homematicip.find_and_load_config_file()
    if config == None:
        log("Cannot find config.ini!")
        sys.exit(1)

    home = homematicip.async_home.AsyncHome()
    asyncio.run(home.init_async(config.access_point,config.auth_token))
    asyncio.run(home.get_current_state_async())

    # Make sure we have all the rooms that have thermostats or thermometers, even those that are not in our config!
    get_rooms()

    # Apply config to rooms
    for section_name in inisections:
        if "room_prefix" in inisections[section_name]:
            for room in rooms:
                if room.startswith(inisections[section_name]["room_prefix"]):
                    rooms[room]=inisections[section_name].copy()
                    rooms[room]["influx_name"]=section_name
                    rooms[room]["subroom"]=room.removeprefix(inisections[section_name]["room_prefix"])
                    rooms[room]["subroom"]=rooms[room]["subroom"].strip()
        else:
            if section_name in rooms:
                rooms[section_name]=inisections[section_name]

    # Initialize some status variables and pick up globals
    for room in rooms:
        rooms[room]["in_event"] =  False
        rooms[room]["boostLastSet"] = datetime.datetime(1970,1,1,tzinfo=datetime.timezone.utc)
        rooms[room]["night_mode"] = False
        if not "influx_name" in rooms[room]:
            rooms[room]["influx_name"]=room
        if not "subroom" in rooms[room]:
            rooms[room]["subroom"]=""
        for key in [ "high", "low", "lown", "ramp", "night_start", "night_end", "summary_keyword", "veto_resource" ]:
            if not key in rooms[room] and key in global_config:
                rooms[room][key]=global_config[key]

    # influxdb for logging
    if "influxhost" in global_config:
        try:
            influx=InfluxDBClient(global_config["influxhost"],global_config["influxport"],database=global_config["influxdb"])
        except:
            log(f'Could not setup InfluxDB client. Bye.')
            sys.exit(1)
    else:
        influx=None
    asyncio.run(main_loop())

