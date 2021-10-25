#
#    Copyright (c) 2013-2021 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#

""" The default schema for the Brultech GEM. """

# Reducing these parameters to the minimum you're likely to need can save considerable database space:
MAX_CURRENT_CHANNELS = 32
MAX_TEMPERATURE_CHANNELS = 8
MAX_PULSE_CHANNELS = 4

table = [
            ('dateTime', 'INTEGER NOT NULL UNIQUE PRIMARY KEY'),
            ('usUnits', 'INTEGER NOT NULL'),
            ('interval', 'INTEGER NOT NULL'),
            ('ch1_volt', 'REAL')
            ] + \
        [('ch%d_a_energy2' % (i + 1), 'INTEGER') for i in range(MAX_CURRENT_CHANNELS)] + \
        [('ch%d_temperature' % (i + 1), 'REAL') for i in range(MAX_TEMPERATURE_CHANNELS)] + \
        [('ch%d_count' % (i + 1), 'INTEGER') for i in range(MAX_PULSE_CHANNELS)]

day_summaries = [(e[0], 'scalar') for e in table if e[0] not in ('dateTime', 'usUnits', 'interval')]

schema = {
    'table': table,
    'day_summaries' : day_summaries
}
