#
#    Copyright (c) 2021-2022 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""Installer for weewx-brultech"""

from distutils.version import StrictVersion
from io import StringIO
import fnmatch
import os

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
        port = 8000
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
    [[SeasonsPowerReport]]
        skin = SeasonsPower
        enable = True
        data_binding = bt_binding                                    
        [[[Units]]]
            [[[[Groups]]]]
                group_energy2 = kilowatt_hour
            
    [[StandardPowerReport]]
        skin = StandardPower
        enable = False
        data_binding = bt_binding                                    
        [[[Units]]]
            [[[[Groups]]]]
                group_energy2 = kilowatt_hour
            
"""

defaults_dict = configobj.ConfigObj(StringIO(BRULTECH_DEFAULTS), encoding='utf-8')


class WeepwrInstaller(ExtensionInstaller):
    def __init__(self):
        super(WeepwrInstaller, self).__init__(
            version="2.2.0",
            name='weewx-brultech',
            description='Extensions to the weewx weather system for Brultech energy monitors.',
            author="Thomas Keffer",
            author_email="tkeffer@gmail.com",
            config=defaults_dict,
            data_services='user.brultech.BrultechService',
            files=[('bin/user',
                    ['bin/user/gem_schema.py',
                     'bin/user/brultech.py']),
                   ('skins/StandardPower',
                    ['skins/StandardPower/skin.conf',
                     'skins/StandardPower/year.html.tmpl',
                     'skins/StandardPower/weewx.css',
                     'skins/StandardPower/week.html.tmpl',
                     'skins/StandardPower/month.html.tmpl',
                     'skins/StandardPower/index.html.tmpl',
                     'skins/StandardPower/favicon.ico',
                     'skins/StandardPower/backgrounds/band.gif',
                     'skins/StandardPower/Summary/Summary-YYYY.txt.tmpl',
                     'skins/StandardPower/Summary/Summary-YYYY-MM.txt.tmpl']),
                   ('skins/SeasonsPower',
                    ['skins/SeasonsPower/skin.conf',
                     'skins/SeasonsPower/about.inc',
                     'skins/SeasonsPower/identifier.inc',
                     'skins/SeasonsPower/current.inc',
                     'skins/SeasonsPower/index.html.tmpl',
                     'skins/SeasonsPower/hilo.inc',
                     'skins/SeasonsPower/titlebar.inc',
                     'skins/SeasonsPower/power.js',
                     'skins/SeasonsPower/rss.xml.tmpl',
                     'skins/SeasonsPower/statistics.html.tmpl',
                     'skins/SeasonsPower/power.css',
                     'skins/SeasonsPower/statistics.inc',
                     'skins/SeasonsPower/favicon.ico',
                     'skins/SeasonsPower/tabular.html.tmpl',
                     'skins/SeasonsPower/POWER/POWER-YYYY-MM.txt.tmpl',
                     'skins/SeasonsPower/POWER/POWER-YYYY.txt.tmpl',
                     'skins/SeasonsPower/font/Kanit-Bold.ttf',
                     'skins/SeasonsPower/font/OpenSans-Regular.ttf',
                     'skins/SeasonsPower/font/Kanit-Regular.ttf',
                     'skins/SeasonsPower/font/OFL.txt',
                     'skins/SeasonsPower/font/OpenSans.woff2',
                     'skins/SeasonsPower/font/OpenSans.woff',
                     'skins/SeasonsPower/font/license.txt',
                     'skins/SeasonsPower/font/OpenSans-Bold.ttf',
                     'skins/SeasonsPower/lang/en.conf']),
            ]
        )

    def configure(self, engine):
        engine.config_dict['Station']['station_type'] = 'Brultech'
        engine.config_dict['StdArchive']['data_binding'] = 'bt_binding'
        return True
