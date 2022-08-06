"""Convert a version 1 style power database to a version 2 style.

Calculate "delta" energies from accumulated energies and add them to the archive.
"""
import argparse
import sys
import time

import weedb
import weeutil.weeutil
import weewx.manager

import brultech

description = """Convert older-style weewx-brultech databases that stored accumulated energy, to one
that stores delta energies. 

The accumulated energies will be left untouched. New columns with delta energies will be added.

                 SQLITE ONLY!
"""

usage = """%(prog)s --help
       %(prog)s SQLITE-FILE"""

Need to update interval as well!

SQL_UPDATE_TEMPLATE = """UPDATE archive
SET %(delta_name)s =
        (WITH archive_lag AS (SELECT dateTime AS dt,
                                     %(energy_name)s AS this_val,
                                     LAG(%(energy_name)s) OVER (ORDER BY dateTime) AS prev_val
                              FROM archive)
         SELECT this_val - prev_val
         FROM archive_lag
         WHERE archive.dateTime = archive_lag.dt);"""


def main():
    parser = argparse.ArgumentParser(description=description, usage=usage,
                                     prog='calc_deltas')
    parser.add_argument("sqlite_file", nargs='?', metavar="SQLITE_FILE")
    namespace = parser.parse_args()

    if not namespace.sqlite_file:
        exit("No path to SQLite file. Nothing done.")

    db_manager = weewx.manager.DaySummaryManager.open({'database_name': namespace.sqlite_file,
                                                       'driver': 'weedb.sqlite'})

    energy_names = sorted(filter(lambda key: brultech.energy2_re.match(key), db_manager.daykeys))
    energy_names.sort(key=weeutil.weeutil.natural_keys)

    print("Ready to convert columns", *energy_names)

    ans = weeutil.weeutil.y_or_n("Drop daily summaries and proceed y/n? ")
    if ans == 'n':
        exit("Nothing done")

    db_manager.drop_daily()
    print("Dropped daily summaries")

    print("Adding new columns")
    t1 = time.time()

    for energy_name in energy_names:
        delta_name = energy_name.replace("_energy2", "d_energy2")

        with weedb.Transaction(db_manager.connection) as cursor:
            cursor.execute("ALTER TABLE archive ADD COLUMN %s INTEGER" % delta_name)
            sql_update = SQL_UPDATE_TEMPLATE % {'energy_name': energy_name, 'delta_name': delta_name}
            cursor.execute(sql_update)

        print("...added", delta_name, end='\r')
        sys.stdout.flush()

    t2 = time.time()

    print("Finished adding new columns in", "%.2f" % (t2-t1), "seconds")


if __name__ == "__main__":
    main()
