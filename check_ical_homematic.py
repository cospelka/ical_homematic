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

# nagios/icinga plugin to check that ical_homematic is running and does 
# not produce any errors.

import os
import time
import sys

if len(sys.argv) >=2 :
  suffix = "_" + sys.argv[1]
else:
  suffix = ""

def bye(status,statusstr):
  exstring = [ "OK", "WARNING", "CRITICAL", "UNKNOWN" ]
  print(f'{exstring[status]} {statusstr}')
  sys.exit(status)

statfilename=f'/var/local/ical_homematic{suffix}/ical_homematic.msg'

try:
  statfiletime=os.path.getmtime(statfilename)
except:
  bye(3,f' Unable to determine modification time of status file {statfilename}.')

if time.time()-statfiletime > 120:
  bye(3,f' Last modification time of status file {statfilename} is more than 120 seconds ago.')

status=3
statusstr=f'Could not read from {statfilename}'
with open(statfilename) as f:
  try:
    statusarr=f.readlines()
    if statusarr:
        status=int(statusarr[0])
        statusarr.pop(0)
        statusstr=' '.join(statusarr)
    else:
        statusstr=f'Status file {statfilename} is empty.'
  except:
    pass

bye(status,statusstr)
