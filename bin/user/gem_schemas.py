#
#    Copyright (c) 2013-2019 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
# ===============================================================================
# This is a list containing the default schema of the database used by GEM.
#
# You may trim this list of any unused types if you wish, but it will not result
# in as much space savings as you may think --- most of the space is taken up by
# the primary key indexes (type "dateTime").
# ===============================================================================

max_current_channels = 16
max_temperature_channels = 8
max_pulse_channels = 4

table = [
            ('dateTime', 'INTEGER NOT NULL UNIQUE PRIMARY KEY'),
            ('usUnits', 'INTEGER NOT NULL'),
            ('interval', 'INTEGER NOT NULL'),
            ('ch1_volt', 'REAL')
            ] + \
        [('ch%d_a_energy' % (i + 1), 'INTEGER') for i in range(max_current_channels)] + \
        [('ch%d_temperature' % (i + 1), 'REAL') for i in range(max_temperature_channels)] + \
        [('ch%d_count' % (i + 1), 'INTEGER') for i in range(max_pulse_channels)]

day_summaries = [(e[0], 'scalar') for e in table if e[0] not in ('dateTime', 'usUnits', 'interval')]

schema = {
    'table': table,
    'day_summaries' : day_summaries
}
