"""Augment a version 1 style energy database, which stores accumulated energies, with delta energies.

The WeeWX API must be in your PYTHONPATH. Generally, the following will work:

PYTHONPATH=/home/weewx/bin python3 -m calc_deltas /path/to/SQLitedatabase
"""
import argparse
import re
import sys
import time

import weedb
import weeutil.weeutil
import weewx.manager

energy2_re = re.compile(r'^ch[0-9]+(_[ap])?_energy2$')

description = """Augment older-style weewx-brultech databases, which stored only accumulated energy, to one
that stores delta energies as well. 

The accumulated energies will be left untouched. New columns with delta energies will be added. 

SQLITE ONLY!
"""

usage = """%(prog)s --help
       %(prog)s SQLITE-FILE"""

SQL_UPDATE_TEMPLATE = """
WITH archive_lag AS (SELECT dateTime as this_time,
                            LAG(dateTime) OVER (order by dateTime) as prev_time,
                            %(energy_name)s as this_val,
                            LAG(%(energy_name)s) OVER (ORDER BY dateTime) as prev_val
                     FROM archive)
UPDATE archive
SET interval       = (SELECT ifnull((this_time - prev_time) / 60, interval)
                      FROM archive_lag
                      WHERE dateTime = archive_lag.this_time),
    %(delta_name)s = (SELECT (this_val - prev_val) 
                      FROM archive_lag 
                      WHERE dateTime = archive_lag.this_time) 
"""

def main():
    parser = argparse.ArgumentParser(description=description,
                                     usage=usage,
                                     prog='calc_deltas')
    parser.add_argument("sqlite_file",
                        metavar="SQLITE_FILE",
                        help="Path to the SQLite weewx-brultech database")
    namespace = parser.parse_args()

    # It's possible the daily summaries have already been dropped. If so, catch the exception
    # and switch database managers.
    try:
        db_manager = weewx.manager.DaySummaryManager.open({'database_name': namespace.sqlite_file,
                                                           'driver': 'weedb.sqlite'})
    except weedb.OperationalError:
        db_manager = weewx.manager.Manager.open({'database_name': namespace.sqlite_file,
                                                 'driver': 'weedb.sqlite'})

    # Find the names of all the accumulated energy fields. These will look like (for example) 'ch5_a_energy2'
    energy_names = list(filter(lambda key: energy2_re.match(key), db_manager.obskeys))

    # Perform a natural sort before displaying
    energy_names.sort(key=weeutil.weeutil.natural_keys)
    print("Ready to augment columns:", energy_names)

    ans = weeutil.weeutil.y_or_n("Drop daily summaries and proceed y/n? ")
    if ans == 'n':
        exit("Nothing done")

    # Before dropping the daily summaries, check to see if it has already been done
    if hasattr(db_manager, 'daykeys'):
        db_manager.drop_daily()
        print("Dropped daily summaries")

    print("Adding new columns")
    t1 = time.time()

    for energy_name in energy_names:
        delta_name = energy_name.replace("_energy2", "d_energy2")

        with weedb.Transaction(db_manager.connection) as cursor:
            # Try adding the new column. If it already exists, an exception will be raised.
            # Be prepared to catch it.
            try:
                cursor.execute("ALTER TABLE archive ADD COLUMN %s INTEGER" % delta_name)
            except weedb.OperationalError:
                pass
            sql_update = SQL_UPDATE_TEMPLATE % {'energy_name': energy_name, 'delta_name': delta_name}
            cursor.execute(sql_update)

        # Give the user a sense of progress:
        print("...added", delta_name, end='\r')
        sys.stdout.flush()

    t2 = time.time()

    print("Finished adding %d new columns in %.2f seconds" % (len(energy_names), t2 - t1))


if __name__ == "__main__":
    main()
