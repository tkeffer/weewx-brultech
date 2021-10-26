#
#    Copyright (c) 2021 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""Installer for Weepwr"""

from distutils.version import StrictVersion
from io import StringIO

import configobj

import weewx
from weecfg.extension import ExtensionInstaller

REQUIRED_WEEWX = "4.2.0"
if StrictVersion(weewx.__version__) < StrictVersion(REQUIRED_WEEWX):
    raise weewx.UnsupportedFeature("weewx %s or greater is required, found %s"
                                   % (REQUIRED_WEEWX, weewx.__version__))


def loader():
    return WeepwrInstaller()


BRULTECH_DEFAULTS = u"""
[Brultech]

    # See README.md for instructions on how to configure the Brultech devices!!

    # Power is computed as the difference between energy records. How old a 
    # record can be and still be used for this calculation:
    stale = 1800

    # How often to poll the device for data
    poll_interval = 10

    # Max number of channels to emit. Limit is set by hardware (48 for GEM).
    max_channels = 32

    # The type of packet to use. Possible choices are GEMBin48NetTime, GEMBin48Net,
    # or GEMAscii:
    packet_type = GEMBin48NetTime

    # Max number of times to try an I/O operation before declaring an error
    max_tries = 3

    driver = user.brultech

    # The type of connection to use. It should match a section below. 
    # Right now, only 'socket' is supported.
    connection = socket

    # The following is for socket connections: 
    [[socket]]
        host = 192.168.1.104
        port = 8083
        timeout = 20
        # After sending a command, how long to wait before looking for a response    
        send_delay = 0.2

    [[sensor_map]]

[DataBindings]

    [[bt_binding]]
        # The database must match one of the sections in [Databases].
        database = bt_sqlite
        # The name of the table within the database
        table_name = archive
        # The manager handles aggregation of data for historical summaries
        manager = weewx.manager.DaySummaryManager
        # The schema defines the structure of the database.
        # It is *only* used when the database is created.
        schema = user.gem_schema.schema

[Databases]
    [[bt_sqlite]]
        database_name = weepwr.sdb
        database_type = SQLite

[StdReport]
    [[PowerReport]]
        skin = Power
        enable = True
        data_binding = bt_binding                                    
        [[[Units]]]
            [[[[Groups]]]]
                group_energy2 = kilowatt_hour
            
"""

defaults_dict = configobj.ConfigObj(StringIO(BRULTECH_DEFAULTS), encoding='utf-8')


class WeepwrInstaller(ExtensionInstaller):
    def __init__(self):
        super(WeepwrInstaller, self).__init__(
            version="1.0.0",
            name='weewx-brultech',
            description='Extensions to the weewx weather system for Brultech energy monitors.',
            author="Thomas Keffer",
            author_email="tkeffer@gmail.com",
            config=defaults_dict,
            data_services='user.brultech.BrultechService',
            files=[('bin/user',
                    ['bin/user/brultech.py',
                     'bin/user/gem_schema.py']),
                   ('skins/Power/backgrounds',
                    ['skins/Power/backgrounds/band.gif']),
                   ('skins/Power',
                    ['skins/Power/favicon.ico',
                     'skins/Power/index.html.tmpl',
                     'skins/Power/month.html.tmpl',
                     'skins/Power/skin.conf',
                     'skins/Power/Summary-YYYY-MM.txt.tmpl',
                     'skins/Power/week.html.tmpl',
                     'skins/Power/weewx.css',
                     'skins/Power/year.html.tmpl'])
                   ]
        )

    def configure(self, engine):
        engine.config_dict['Station']['station_type'] = 'Brultech'
        engine.config_dict['StdArchive']['data_binding'] = 'bt_binding'
        return True