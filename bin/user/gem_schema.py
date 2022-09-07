#
#    Copyright (c) 2013-2022 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#

""" The default schema for the Brultech GEM. """

# Reducing these parameters to the minimum you're likely to need can save considerable database space:
MAX_CURRENT_CHANNELS = 32
MAX_TEMPERATURE_CHANNELS = 8
MAX_PULSE_CHANNELS = 4
INCLUDE_ACCUMULATED_ABSOLUTE = True
INCLUDE_ACCUMULATED_POLARIZED = False
INCLUDE_DELTA_ABSOLUTE = True
INCLUDE_DELTA_POLARIZED = False
INCLUDE_POWER_ABSOLUTE = False
INCLUDE_POWER_POLARIZED = False

table = [
            ('dateTime', 'INTEGER NOT NULL UNIQUE PRIMARY KEY'),
            ('usUnits', 'INTEGER NOT NULL'),
            ('interval', 'INTEGER NOT NULL'),
            ('ch1_volt', 'REAL')
            ] + \
        [('ch%d_temperature' % (i + 1), 'REAL') for i in range(MAX_TEMPERATURE_CHANNELS)] + \
        [('ch%d_count' % (i + 1), 'INTEGER') for i in range(MAX_PULSE_CHANNELS)]

if INCLUDE_ACCUMULATED_ABSOLUTE:
    table += [('ch%d_a_energy2' % (i + 1), 'BIGINT') for i in range(MAX_CURRENT_CHANNELS)]
if INCLUDE_ACCUMULATED_POLARIZED:
    table += [('ch%d_p_energy2' % (i + 1), 'BIGINT') for i in range(MAX_CURRENT_CHANNELS)]
if INCLUDE_DELTA_ABSOLUTE:
    table += [('ch%d_ad_energy2' % (i + 1), 'INTEGER') for i in range(MAX_CURRENT_CHANNELS)]
if INCLUDE_DELTA_POLARIZED:
    table += [('ch%d_pd_energy2' % (i + 1), 'INTEGER') for i in range(MAX_CURRENT_CHANNELS)]
if INCLUDE_POWER_ABSOLUTE:
    table += [('ch%d_a_power' % (i + 1), 'FLOAT') for i in range(MAX_CURRENT_CHANNELS)]
if INCLUDE_POWER_POLARIZED:
    table += [('ch%d_p_power' % (i + 1), 'FLOAT') for i in range(MAX_CURRENT_CHANNELS)]


day_summaries = [(e[0], 'scalar') for e in table if e[0] not in ('dateTime', 'usUnits', 'interval')]

schema = {
    'table': table,
    'day_summaries' : day_summaries
}
