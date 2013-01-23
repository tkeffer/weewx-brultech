#
#    Copyright (c) 2013 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#

import weewx.accum
import weewx.wxengine
import weeutil.weeutil

class GEMAccum(weewx.accum.BaseAccum):
    """Subclass of BaseAccum, which adds logic specific to the GEM."""

    def addRecord(self, record):
        """Add a record to my running statistics. 
        
        The record must have keys 'dateTime' and 'usUnits'.
        
        This is a GEM-specific version."""
        
        # Check to see if the record is within my observation timespan 
        if not self.timespan.includesArchiveTime(record['dateTime']):
            raise weewx.accum.OutOfSpan, "Attempt to add out-of-interval record"

        # This is pretty much like the loop in my superclass's version, except
        # ignore the serial number (which is a string).
        for obs_type in record:
            if obs_type in ['serial']:
                continue
            else:
                self._add_value(record[obs_type], obs_type, record['dateTime'])
            
    def getRecord(self):
        """Extract a record out of the results in the accumulator.
        
        This is a GEM-specific version. """
        # All records have a timestamp and unit type
        record = {'dateTime': self.timespan.stop,
                  'usUnits' : self.unit_system}
        # Go through all observation types.
        for obs_type in self:
            if obs_type.endswith('_aws') or obs_type.endswith('_wh'):
                record[obs_type] = self[obs_type].last
            elif obs_type.endswith('_dwh') or obs_type.endswith('_nwh') or obs_type.endswith('_pwh'):
                record[obs_type] = self[obs_type].sum
            else:
                # For everything else, we want the average:
                record[obs_type] = self[obs_type].avg
        return record

class GEMArchive(weewx.wxengine.StdArchive):
    
    def _new_accumulator(self, timestamp):
        start_archive_ts = weeutil.weeutil.startOfInterval(timestamp,
                                                           self.archive_interval)
        end_archive_ts = start_archive_ts + self.archive_interval
        
        new_accumulator =  GEMAccum(weeutil.weeutil.TimeSpan(start_archive_ts, end_archive_ts))
        return new_accumulator
    