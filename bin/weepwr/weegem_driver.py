"""Driver for the weewx engine."""

import weepwr.gem_driver
import weewx.abstractstation


def loader(config_dict):

    source = GEMWeewx.source_factory(**config_dict['GEM'])
    station = GEMWeewx(source, **config_dict['GEM'])
    station.setup(send_interval=int(config_dict['GEM'].get('packet_interval', 5)))
    return station

class GEMWeewx(weepwr.gem_driver.GEM, weewx.abstractstation.AbstractStation):
    
    def __init__(self, source, **gem_dict) :
        """Initialize an object of type GEMWeewx. """
        # Initialize my base class:
        weepwr.gem_driver.GEM.__init__(self, source, **gem_dict)
    
    @staticmethod
    def source_factory(**gem_dict):
        
        if gem_dict['type'] == 'ethernet':
            source = weepwr.gem_driver.SocketConnection.open(**gem_dict)
            return source
        else:
            raise NotImplementedError("Source type unknown ('%s')" % gem_dict['type'])
        
    def genLoopPackets(self):
        # Generate packets from my base class...
        for packet in weepwr.gem_driver.GEM.genLoopPackets(self):
            # ... then add the weewx unit type:
            packet['usUnits'] = weewx.US
            yield packet
    
if __name__ == '__main__':

    import optparse
    import configobj
    import weeutil.weeutil
    
    parser = optparse.OptionParser()
    (options, args) = parser.parse_args()
    config_dict = configobj.ConfigObj(args[0], file_error=True)
    
    station = loader(config_dict)

    for ipacket, packet in enumerate(station.genLoopPackets()):
        print ipacket, weeutil.weeutil.timestamp_to_string(packet['time_created']), packet['volts'],

        for key in packet:
            if key.startswith('ch8'):
                print " %s = %s," % (key, packet[key]),
        
        print