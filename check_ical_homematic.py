#!/usr/bin/python3
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
import time
import sys

def bye(status,statusstr):
  exstring = [ "OK", "WARNING", "CRITICAL", "UNKNOWN" ]
  print(f'{exstring[status]}{statusstr}')
  sys.exit(status)

status=3

statfilename=/usr/local/var/ical_homematic/ical_homematic.msg
statusstr=f' Unable to read status file {statfilename}.'

try:
  statfiletime=os.path.getmtime(statfilename)
except:
  statusstr=f' Unable to determine modification time of status file {statfilename}.'
  bye(status,statusstr)

if time.time()-statfiletime > 120:
  statusstr=f' Last modification time of status file {statfilename} is more than 120 seconds ago.'
  bye(status,statusstr)

with open(statfilename) as f:
  try:
    statusstr=f.readline()
    if statusstr:
        status=2
    else:
        status=0
  except:
    pass

bye(status,statusstr)
