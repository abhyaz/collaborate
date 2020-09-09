#
# Usage: teacher-desktop(MAX-DIMENSION, MIN-DIMENSION)

import subprocess
import multiprocessing

import sys
import os
import json
import math
import time
import psutil
import re
import signal

import psycopg2

from lxml import etree

from .simple_text import simple_text
from .bigbluebutton import getMeetingInfo

VIEWONLY_VIEWER = "/home/baccala/src/ssvnc-1.0.29/vnc_unixsrc/vncviewer/vncviewer"

VALID_DISPLAYS = []
NAMES = dict()

conn = None

# No password needed to connect to localhost when Postgres is configured for "trust" authentication.

postgreshost = 'localhost'
postgresdb = 'greenlight_production'
postgresuser = 'postgres'
postgrespw = None

def open_database():
    global conn
    conn = psycopg2.connect(database=postgresdb, host=postgreshost, user=postgresuser, password=postgrespw)

def get_VALID_DISPLAYS_and_NAMES():

    # We look at the system process table for Xtightvnc processes
    # and match them to the "fullName"s of VIEWERS in the 'osito'
    # meeting.  The fullNames get converted to UNIX usernames
    # using the VNCusers table in the Postgres database.

    if not conn:
        open_database()

    VALID_DISPLAYS.clear()
    NAMES.clear()

    meetingInfo = getMeetingInfo('osito')

    running_commands = list(psutil.process_iter(['cmdline']))

    for e in meetingInfo.xpath(".//role[text()='VIEWER']/../fullName"):

        UNIXuser = None

        with conn.cursor() as cur:
            try:
                cur.execute("SELECT UNIXuser FROM VNCusers WHERE VNCuser = %s", (e.text,))
                row = cur.fetchone()
                if row:
                    UNIXuser = row[0]
            except psycopg2.DatabaseError as err:
                print(err)
                cur.execute('ROLLBACK')

        if UNIXuser:
            for proc in running_commands:
                cmdline = proc.info['cmdline']
                if len(cmdline) > 0 and 'Xtightvnc' in cmdline[0]:
                    m = re.search('/home/{}/\.vnc'.format(UNIXuser), ' '.join(cmdline))
                    if m:
                        VALID_DISPLAYS.append(cmdline[1])
                        NAMES[cmdline[1]] = e.text

# 'processes' maps display names to a list of processes associated
# with them.  Each one will have a vncviewer and a Tk label.

processes = dict()

# 'locations' maps display names to their location in the on-screen grid.

locations = dict()

def kill_processes(list_of_procs):
    r"""
    Kills a list of processes that were created with either
    subprocess.Popen or multiprocessing.Process
    """

    for proc in list_of_procs:
        if isinstance(proc, subprocess.Popen):
            proc.kill()
        elif isinstance(proc, multiprocessing.Process):
            proc.terminate()

def main_loop():

    global processes
    global locations

    get_VALID_DISPLAYS_and_NAMES()

    old_cols = math.ceil(math.sqrt(len(locations)))
    cols = math.ceil(math.sqrt(len(VALID_DISPLAYS)))

    # If the number of clients changed enough to require a resize of
    # the entire display grid, kill all of our old processes,
    # triggering a rebuild of the entire display.

    # Otherwise, kill the processes for displays that are no longer
    # valid.  This is necessary to avoid a situation where we don't
    # have enough display slots available for the VALID_DISPLAYS,
    # which would trigger an exception a little later.

    if old_cols != cols:
        for procs in processes.values():
            kill_processes(procs)
        processes.clear()
        locations.clear()
    else:
        DEAD_DISPLAYS = [disp for disp in processes.keys() if disp not in VALID_DISPLAYS]
        for disp in DEAD_DISPLAYS:
            kill_processes(processes[disp])
            processes.pop(disp)
            locations.pop(disp)

    if cols > 0:

        SCALEX = int(SCREENX/cols - .01*SCREENX)
        SCALEY = int(SCREENY/cols - .01*SCREENY)

        SCALE = str(SCALEX) + "x" + str(SCALEY)

        for display in VALID_DISPLAYS:
            if display not in processes:
                # pick the first screen location not already claimed in locations
                i = [i for i in range(len(VALID_DISPLAYS)) if i not in locations.values()][0]
                locations[display] = i

                processes[display] = []
                row = int(i/cols)
                col = i%cols
                geox = int(col * SCREENX/cols + .005*SCREENX)
                geoy = int(row * SCREENY/cols + .005*SCREENY)
                args = [VIEWONLY_VIEWER, '-viewonly', '-geometry', '+'+str(geox)+'+'+str(geoy),
                        '-scale', SCALE, '-passwd', '/home/baccala/.vnc/passwd', display]
                processes[display].append(subprocess.Popen(args, stderr=subprocess.DEVNULL))

                processes[display].append(simple_text(NAMES[display], geox + SCREENX/cols/2, geoy))



def restore_original_state():
    for procs in processes.values():
        kill_processes(procs)
    subprocess.Popen(["xsetroot", "-solid", "grey"]).wait()
    subprocess.Popen(["fvwm", "-r"])

def signal_handler(sig, frame):
    print('You pressed Ctrl+C!')
    restore_original_state()
    sys.exit(0)

def teacher_desktop(screenx, screeny):

    global SCREENX, SCREENY

    SCREENX = int(screenx)
    SCREENY = int(screeny)

    # When switching to teacher mode, we completely replace the FVWM window manager with a new
    # instance using a completely different config, then switch back to the original config
    # (using yet another new FVWM instance) when we're not.  Not ideal, but it works.

    args = ["fvwm", "-c", "PipeRead 'python3 -m vnc_collaborate print teacher_mode_fvwm_config'", "-r"]
    fvwm = subprocess.Popen(args)

    subprocess.Popen(["xsetroot", "-solid", "black"]).wait()
    main_loop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Big Blue Button currently (version 2.2.22) lacks a mechanism in its REST API
    # to get notifications when users come and go.  So we poll every second...

    while True:
        try:
            fvwm.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            main_loop()

    restore_original_state()
