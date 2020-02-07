#
#    Copyright (c) 2013-2020 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""Classes and functions for interfacing with the Brultech energy monitors.

A simplifying assumption is that the Brultech device is always in server mode,
and the driver only polls it. See README.md for instructions on how to set up the device.
"""
from __future__ import print_function, with_statement

import calendar
import logging
import re
import struct
import sys
import time

try:
    # Python 3
    import io
except ImportError:
    # Python 2
    import StringIO as io

import configobj

import weewx
import weewx.accum
import weewx.drivers
import weewx.engine
import weewx.units
import weewx.xtypes
import weeutil.timediff
from weeutil.weeutil import to_int, to_float
from weewx.units import ValueTuple

log = logging.getLogger(__name__)

DRIVER_NAME = 'Brultech'
DRIVER_VERSION = '0.2.0'

DEFAULTS_INI = u"""
[Brultech]

    # See README.md for instructions on how to configure the Brultech devices!!

    # Power is computed as the difference between energy records. How old a 
    # record can be and still used for this calculation:
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
    
    driver = user.brultech.Brultech

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

"""

brultech_defaults = configobj.ConfigObj(io.StringIO(DEFAULTS_INI))


def loader(config_dict, engine):
    # Start with the defaults. Make a copy --- we will be modifying it
    bt_config = configobj.ConfigObj(brultech_defaults)['Brultech']
    # Now merge in the overrides from the config file
    bt_config.merge(config_dict.get('Brultech', {}))
    # Instantiate and return the driver
    return Brultech(**bt_config.dict())


def configurator_loader(config_dict):  # @UnusedVariable
    return BrultechConfigurator()


# ===============================================================================
#                           Connection Classes
# ===============================================================================

class BaseConnection(object):
    """Abstract base class representing a connection."""

    def __init__(self, send_delay):
        self.send_delay = send_delay

    def close(self):
        pass

    def flush_input(self):
        pass

    def flush_output(self):
        pass

    def queued_bytes(self):
        raise NotImplementedError()

    def read(self, chars=1, max_tries=3):
        raise NotImplementedError()

    def read_with_prompt(self, prompt, n):
        """Write a prompt, then wait for a response. """
        self.write(prompt)
        time.sleep(self.send_delay)
        byte_buf = self.read(n)
        return byte_buf

    def write(self, data):
        raise NotImplementedError()

    def write_with_response(self, prompt, expected, max_tries=3):
        """Write a prompt, then wait for an expected response"""
        for i_try in range(max_tries):
            self.write(prompt)
            time.sleep(self.send_delay)
            length = self.queued_bytes()
            response = self.read(length)
            if weewx.debug >= 2:
                log.debug("For prompt %s got %s", prompt, response)
            if response == expected:
                return
            log.debug("Try %d, for prompt %s", i_try + 1, prompt)
        else:
            log.error("Prompt %s, max tries (%d) exceeded", prompt, max_tries)
            raise weewx.RetriesExceeded("Prompt %s, max tries (%d) exceeded" % (prompt, max_tries))


class SocketConnection(BaseConnection):
    """Wraps a socket connection, supplying simplified access methods."""

    def __init__(self, host, port, send_delay=0.2, timeout=20):
        """Initialize a SocketConnection.

        NAMED ARGUMENTS:

        host: IP host. No default.

        port: The socket to be used. No default.

        send_delay: After sending a command, how long to wait before looking for a response.

        timeout: How long to wait on a socket connection. Default is 20 seconds.
        """
        super(SocketConnection, self).__init__(send_delay)
        import socket

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(timeout)
            self.socket.connect((host, port))
        except (socket.error, socket.herror, socket.gaierror, socket.timeout) as ex:
            log.error("Error while opening socket to host %s on port %d: %s", host, port, ex)
            # Reraise as a WeeWX error:
            raise weewx.WeeWxIOError(ex)
        log.debug("Opened up socket to host %s on port %d", host, port)

    def close(self):
        import socket
        self.socket.shutdown(socket.SHUT_RDWR)
        self.socket.close()

    def flush_input(self):
        """Flush the input buffer"""
        try:
            # This is a bit of a hack, but there is no analogue to pyserial's flushInput()
            old_timeout = self.socket.gettimeout()
            self.socket.settimeout(0)
            # Read a bunch of bytes, but throw them away.
            self.socket.recv(4096)
        except IOError:
            pass
        finally:
            self.socket.settimeout(old_timeout)

    def flush_output(self):
        """Flush the output buffer

        This function does nothing as there should never be anything left in
        the buffer when using socket.sendall()"""
        pass

    def queued_bytes(self):
        """Determine how many bytes are in the socket buffer"""
        import socket
        length = 0
        try:
            old_timeout = self.socket.gettimeout()
            self.socket.settimeout(0)
            length = len(self.socket.recv(8192, socket.MSG_PEEK))
        except socket.error:
            pass
        finally:
            self.socket.settimeout(old_timeout)
        return length

    def read(self, chars=1, max_tries=3):
        """Read bytes, returning them as a bytearray"""
        import socket
        attempt = 0
        buf = bytearray()
        remaining = chars or 4096
        while remaining > 0:
            attempt += 1
            if attempt > max_tries:
                raise weewx.RetriesExceeded("Requested %d bytes, only %d received" % (chars, len(buf)))
            try:
                recv = self.socket.recv(remaining)
            except (socket.timeout, socket.error) as ex:
                # Reraise as a WeeWX error
                raise weewx.WeeWxIOError(ex)
            log.debug("Received %d bytes" % len(recv))
            buf.extend(recv)
            if not chars:
                break
            remaining -= len(buf)
        return buf

    def write(self, data):
        """Write bytes"""
        import socket
        try:
            self.socket.sendall(data)
        except (socket.timeout, socket.error) as ex:
            log.error("Socket write error. %s", ex)
            # Reraise as a WeeWX error:
            raise weewx.WeeWxIOError(ex)


# ===============================================================================
#                           Packet Types
# ===============================================================================

class BTBase(object):
    """Base type for the Brultech packet types.

    Superclasses should provide:

        self.packet_format: The packet format number. See the Brultech Packet Format Guide

    In addition, for binary packets:

        self.packet_ID: Not to be confused with "packet_format" above. This is the byte
        identifier carried by binary packets in index 2.

        self.packet_length: The length of the packet.
    """

    def __init__(self, source):
        self.source = source

    def setup(self):
        """Packet specific setup."""

        # This command sets the type of packet the device will respond with
        self.source.write_with_response(b"^^^SYSPKT%02d" % self.packet_format, b"PKT\r\n")

    def get_packet(self):

        # Request a single packet...
        byte_buf = self.source.read_with_prompt(b'^^^APISPK', self.packet_length)

        # ... check it for integrity ...
        self._check_ends(byte_buf)
        self._check_checksum(byte_buf)
        self._check_ID(byte_buf)

        # ... then extract the packet from the buffer
        packet = self._extract_packet_from(byte_buf)
        return packet

    def _extract_packet_from(self, byte_buf):
        """Extract the packet contents out of a bytebuffer.
        Should be provided by a specializing class."""
        raise NotImplementedError()

    @staticmethod
    def _check_ends(buf):
        if not (buf[0] == buf[-2] == 0xFE) or not (buf[1] == buf[-3] == 0xFF):
            raise weewx.WeeWxIOError('Bad header or footer')

    @staticmethod
    def _check_checksum(buf):
        chksum = sum(buf[:-1])
        if chksum & 0xff != buf[-1]:
            raise weewx.WeeWxIOError("Bad checksum")

    def _check_ID(self, buf):
        if self.packet_ID != buf[2]:
            raise weewx.WeeWxIOError("Bad packet ID. Got %d, expected %d" % (buf[2], self.packet_ID))


class GEMAscii(BTBase):
    """Implements 'ASCII with WH' packet (format #2)"""

    def __init__(self, source, **bt_dict):
        super(GEMAscii, self).__init__(source)
        self.packet_length = None
        self.packet_format = 2

    def get_packet(self):
        # Request a single packet of unknown length
        byte_buf = self.source.read_with_prompt(b'^^^APISPK', None)

        # Extract the packet from the buffer
        packet = self._extract_packet_from(byte_buf)
        return packet

    def _extract_packet_from(self, byte_buf):
        try:
            # Python 3
            # noinspection PyUnresolvedReferences
            from urllib.parse import parse_qsl
        except ImportError:
            # Python 2
            # noinspection PyUnresolvedReferences
            from urlparse import parse_qsl

        tuple_buf = parse_qsl(byte_buf)

        packet = {}
        for obs_type, val in tuple_buf:
            if obs_type == b'n':
                packet['ser_no'] = int(val)
            elif obs_type == b'm':
                packet['secs'] = int(val) * 60
            elif obs_type == b'v':
                packet['ch1_volt'] = float(val)
            else:
                underscore = obs_type.find(b'_')
                channel = int(obs_type[underscore + 1:])
                obs_generic = obs_type[:underscore].decode('ascii')
                if obs_generic == 'wh':
                    packet['ch%d_energy2' % channel] = int(float(val) * 3600 + 0.5)  # Convert to watt-sec
                elif obs_generic == 'p':
                    packet['ch%d_power' % channel] = float(val)
                elif obs_generic == 'a':
                    packet['ch%d_amp' % channel] = float(val)
                elif obs_generic == 't':
                    packet['ch%d_temperature' % channel] = float(val)
                elif obs_generic == 'p':
                    packet['ch%d_count' % channel] = int(val)
                else:
                    log.debug('Unrecognized observation type', obs_type)

        return packet


class GEMBin48Net(BTBase):
    """Implement the GEM BIN48-NET (format #5) packet"""

    def __init__(self, source, **bt_dict):
        super(GEMBin48Net, self).__init__(source)
        self.packet_length = 619
        self.packet_ID = 5
        self.packet_format = 5
        self.max_channels = to_int(bt_dict.get('max_channels', 32))

    def _extract_packet_from(self, byte_buf):
        packet = {
            'dateTime': int(time.time() + 0.5),
            'usUnits': weewx.METRICWX,
            'ch1_volt': float(struct.unpack('>H', byte_buf[3:5])[0]) / 10.0,
            'ser_no': struct.unpack('>H', byte_buf[485:487])[0],
            'unit_id': byte_buf[488],
            'secs': unpack(byte_buf[585:588])
        }

        # Form the full, formatted serial number:
        packet['serial'] = '%03d%05d' % (packet['unit_id'], packet['ser_no'])

        # Extract absolute watt-seconds:
        aws = extract_seq(byte_buf[5:], self.max_channels, 5, 'ch%d_a_energy2')
        packet.update(aws)

        # Extract polarized watt-seconds:
        pws = extract_seq(byte_buf[245:], self.max_channels, 5, 'ch%d_p_energy2')
        packet.update(pws)

        # Extract current:
        current = extract_seq(byte_buf[489:], self.max_channels, 2, 'ch%d_amp')
        # Divide by 50, as per GEM manual
        for x in current:
            current[x] /= 50.0
        packet.update(current)

        # Extract pulses:
        pulse = extract_seq(byte_buf[588:], 4, 3, 'ch%d_count')
        packet.update(pulse)

        # Extract temperatures:
        for i in range(8):
            t = _mktemperature(byte_buf[600 + 2 * i:])
            # Ignore any out of range temperatures; keep the rest
            if abs(t) <= 255:
                packet['ch%d_temperature' % (i + 1)] = t

        return packet


class GEMBin48NetTime(GEMBin48Net):
    """Implement the GEM BIN48-NET-TIME (format #4) packet"""

    def __init__(self, source, **bt_dict):
        super(GEMBin48NetTime, self).__init__(source, **bt_dict)
        self.packet_length = 625
        self.packet_format = 4

    def _extract_packet_from(self, byte_buf):
        # Get the packet from my superclass
        packet = GEMBin48Net._extract_packet_from(self, byte_buf)

        # Add the embedded time:
        Y, M, D, h, m, s = byte_buf[616:622]
        time_tt = (Y + 2000, M, D, h, m, s, 0, 0, -1)
        packet['time_created'] = int(calendar.timegm(time_tt))

        return packet


# ===============================================================================
#                                The driver
# ===============================================================================

class Brultech(weewx.drivers.AbstractDevice):

    def __init__(self, **bt_dict):
        """Initialize from a config dictionary

        NAMED ARGUMENTS:

        packet_type: The class for the expected packet type. Default is GEMBin48NetTime.

        connection: Type of connection. Default is 'socket'

        poll_interval: How often to poll for data. Default is 5.

        max_tries: The maximum number of tries that should be attempted in the event
        of a read error. Default is 3.

        # In addition, there should be a subdictionary with key matching 'connection'
        above. For example, for sockets:
        socket: { 'host':  192.168.1.101, 'port': 8086, 'timeout': 20 }
        """

        self.poll_interval = to_float(bt_dict.get('poll_interval', 5))
        self.max_tries = to_int(bt_dict.get('max_tries', 3))
        packet_type = bt_dict.get('packet_type', 'GEMBin48NetTime')
        connection_type = bt_dict.get('connection', 'socket')
        self.source = source_factory(connection_type, bt_dict)
        # Instantiate the packet class:
        self.packet_obj = globals()[packet_type](self.source, **bt_dict)
        self.setup()

    def setup(self):
        """Initialize the device to start sending packets."""

        self.source.flush_input()
        # Turn off real-time mode.
        self.source.write_with_response(b"^^^SYSOFF", b"OFF\r\n")
        # This turns off the "keep-alive" feature
        self.source.write_with_response(b"^^^SYSKAI0", b"OK\r\n")
        # This sets the temperature units to Celsius.
        self.source.write_with_response(b'^^^TMPDGC', b'C')

        # Let the packet type set things up:
        self.packet_obj.setup()

    @property
    def hardware_name(self):
        return "Brultech"

    def closePort(self):
        self.source.close()

    def genLoopPackets(self):
        """Generator function that returns packets."""

        while True:
            packet = self.packet_obj.get_packet()
            yield packet
            t = time.time()
            target_time = int(t / self.poll_interval + 1) * self.poll_interval
            time.sleep(target_time - t)

    def getTime(self):
        """Get the time on the Brultech device."""

        # Can't figure out any other way to do this. Poll the device for a current packet:
        packet = self.packet_obj.get_packet()
        if 'time_created' in packet:
            return packet['time_created']
        raise NotImplementedError

    def setTime(self):
        """Set the time"""

        # Unfortunately, clock resolution is only 1 second, and transmission takes a
        # little while to complete, so round up the clock up. 0.5 for clock resolution
        # and 0.25 for transmission delay. Also, the Brultech uses UTC.
        newtime_tt = time.gmtime(int(time.time() + 0.75))
        # Extract the year, month, etc.
        y, mo, d, h, mn, s = newtime_tt[0:6]
        # Year should be given modulo 2000
        y -= 2000
        # Form the byte-string.
        time_str = b",".join([b"%02d" % x for x in (y, mo, d, h, mn, s)])
        # Send the command
        self.source.write_with_response(b"^^^SYSDTM%s\r" % time_str, b"DTM\r\n")
        log.debug("Set time to '%s'" % time_str)

    def get_info(self):
        all = self.source.read_with_prompt(b'^^^RQSALL', None)
        return all


def source_factory(source_name, bt_dict):
    """Instantiate and return a source of type 'source_name'.

    Presently, we only support type 'socket'
    """
    if source_name == 'socket':
        host = bt_dict['socket']['host']
        port = to_int(bt_dict['socket']['port'])
        send_delay = to_float(bt_dict['socket'].get('send_delay', 0.2))
        timeout = to_int(bt_dict['socket'].get('timeout', 20))
        return SocketConnection(host=host, port=port, send_delay=send_delay, timeout=timeout)
    else:
        raise weewx.ViolatedPrecondition("Unknown connection type %s" % source_name)


# ===============================================================================
#                      Class BrultechConfigurator
# ===============================================================================

class BrultechConfigurator(weewx.drivers.AbstractConfigurator):
    @property
    def description(self):
        return "Configures the Brultech energy monitors."

    @property
    def usage(self):
        return """%prog [config_file] [--help] [--info]"""

    def add_options(self, parser):
        super(BrultechConfigurator, self).add_options(parser)
        parser.add_option("--info", action="store_true", dest="info",
                          help="To print configuration information.")

    def do_options(self, options, parser, config_dict, prompt):
        device = Brultech(**config_dict[DRIVER_NAME])
        if options.info:
            self.show_info(device)

    @staticmethod
    def show_info(device, dest=sys.stdout):
        """Query the configuration of the Vantage, printing out status
        information"""

        print("Querying...")
        all = device.get_info()
        print(all, file=dest)


# ===============================================================================
#                            Packet Utilities
# ===============================================================================

def unpack(a):
    """Unpack a value from a byte array, little endian order."""
    total = 0
    # Work in reverse order, so the highest order byte is first
    for b in a[::-1]:
        # Bump our total so far 8 bits to the left
        total <<= 8
        # Add in this byte
        total += b
    return total


def extract_seq(buf, N, nbyte, tag):
    results = {}
    for i in range(N):
        x = unpack(buf[i * nbyte:i * nbyte + nbyte])
        results[tag % (i + 1)] = x
    return results


# Adapted from btmon.py
def _mktemperature(b):
    """Decode temperature from a 2 element bytearray"""

    # Temperature is held in two bytes, little-endian order. The value needs to be divided by 2, as per the appendix
    # "Binary Packet Fields", in the Brultech document "GEM Packet Format" (GEM-PKT, Ver 2.1). The sign is held in the
    # upper bit of the second byte (NB: this is different from the more usual way of storing negative numbers using
    # two's complement).

    # Calculate the value and divide by 2
    t = ((b[1] & 0x7f) << 8 | b[0]) / 2.0

    # Check the sign
    if b[1] & 0x80:
        t = -t

    return t


# ===============================================================================
#                            Configuration
# ===============================================================================

# Regular expressions for the various channel types
volt_re = re.compile(r'^ch[0-9]+_volt$')
temperature_re = re.compile(r'^ch[0-9]+_temperature$')
energy2_re = re.compile(r'^ch[0-9]+(_[ap])?_energy2$')
count_re = re.compile(r'^ch[0-9]+_count$')
power_re = re.compile(r'^ch[0-9]+(_[ap])?_power$')


class BTAccumConfig(object):
    """Fake dictionary that, when keyed with Brultech observation types, returns
    the proper accumulator configuration for that type.

    - volts and temperatures can be treated normally.
    - Energy and counts are extensive variables that require special treatment.
    - 'ser_no' and 'serial' are strings and will require a string accumulator.
    """

    def __getitem__(self, key):
        global volt_re, temperature_re, energy2_re, count_re
        if volt_re.match(key) or temperature_re.match(key):
            # These are intensive quantities. The defaults will do.
            return weewx.accum.OBS_DEFAULTS
        elif energy2_re.match(key) or count_re.match(key) \
                or key in ('time_created', 'secs', 'unit_id'):
            # These are extensive quantities. We need the last value (rather than the average).
            return {'extractor': 'last'}
        elif key == 'ser_no' or key == 'serial':
            # These are strings
            return {'accumulator': 'firstlast', 'extractor': 'last'}
        else:
            # Don't know what it is. Raise a KeyError
            raise KeyError(key)

    def __contains__(self, key):
        """Does key match any type we know about?"""
        global volt_re, temperature_re, energy2_re, count_re
        return key in ('time_created', 'secs', 'unit_id', 'ser_no', 'serial') \
               or volt_re.match(key) \
               or temperature_re.match(key) \
               or energy2_re.match(key) \
               or count_re.match(key)


# ===============================================================================
#                   Classes for the WeeWX XTypes system
# ===============================================================================

class BTObsGroupDict(object):
    """Fake dictionary that, when keyed with Brultech observation types, returns
    the unit group it belongs to.

    For example, when keyed with 'ch4_a_power', it returns 'group_power'.
    """

    def __getitem__(self, key):
        global volt_re, temperature_re, energy2_re, count_re, power_re
        if key == 'time_created':
            return "group_time"
        elif key == 'secs':
            return "group_deltatime"
        elif volt_re.match(key) \
                or temperature_re.match(key) \
                or energy2_re.match(key) \
                or count_re.match(key) \
                or power_re.match(key):
            # For these, the type can be inferred from the observation name.
            underscore = key.rfind('_')
            if underscore == -1:
                return None
            group = "group%s" % key[underscore:]
            return group
        else:
            # Don't know what it is. Raise KeyError
            raise KeyError(key)

    def __contains__(self, key):
        global volt_re, temperature_re, energy2_re, count_re, power_re
        return key == 'time_created' \
               or key == 'secs' \
               or volt_re.match(key) \
               or temperature_re.match(key) \
               or energy2_re.match(key) \
               or count_re.match(key) \
               or power_re.match(key)


class BTExtends(weewx.xtypes.XType):
    """Extensions for the WeeWX extensible type system. It performs three functions:
    1. Add power types to a packet. These are calculated from time differences of energy.
    2. Calculate a scalar power from energy, using data in the database.
    3. Calculate a series power from energy, using data in the database.
    """

    def __init__(self, bt_dict):
        self.stale = to_int(bt_dict.get('stale', 1800))
        self.derivatives = {}
        self.prev_record = None

    def add_power_to_packet(self, packet):
        """Calculate and add power for all energy channels that appear in a packet"""
        global energy2_re
        # Scan through the packet... (We will be changing the size of the packet, so we need to scan through
        # a static list to avoid Python 3 errors).
        for obs_type in list(packet.keys()):
            # ... looking for energy keys that we recognize.
            if energy2_re.match(obs_type):
                # Have we seen this type before? If not, create a new TimeDerivative object for it
                if obs_type not in self.derivatives:
                    self.derivatives[obs_type] = weeutil.timediff.TimeDerivative(obs_type, self.stale)
                # Add the packet to the TimeDerivative object, getting the derivative in return. If there isn't enough
                # information to calculate the derivative, an exception may be raised. Be prepared to catch it.
                try:
                    deriv = self.derivatives[obs_type].add_record(packet)
                    # Get the name for power. This will turn something like ch5_a_energy2 to ch5_a_power
                    power_name = obs_type.replace('energy2', 'power')
                    # Save it under that name
                    packet[power_name] = deriv
                except weewx.CannotCalculate:
                    pass

    def get_scalar(self, obs_type, record, db_manager):
        """Calculate a Brultech derived observation type.

        This version only knows how to calculate power from energy2.
        """
        self.prev_record = None

        # Get the corresponding energy name from the power name. This replaces something like ch5_a_power
        # with ch5_a_energy2:
        energy2_name = obs_type.replace('power', 'energy2')

        # We only know how to calculate power. Ignore others.
        if not energy2_re.match(energy2_name):
            raise weewx.UnknownType(obs_type)
        # We require that the energy value be in the record
        if not record or energy2_name not in record:
            raise weewx.CannotCalculate(obs_type)

        prev_ts = record['dateTime'] - record['interval'] * 60
        # See if we've cached a record with the right timestamp
        if not self.prev_record or record['dateTime'] != prev_ts:
            # No. Go get it.
            self.prev_record = db_manager.getRecord(prev_ts)

        if record[energy2_name] is not None and self.prev_record.get(energy2_name) is not None:
            deriv = (record[energy2_name] - self.prev_record.get(energy2_name)) \
                    / (record['dateTime'] - self.prev_record['dateTime'])
        else:
            deriv = None
        return ValueTuple(deriv, 'watt', 'group_power')

    # This SQL statement uses an inner join to calculate the derivative over a time interval.
    SQL_TEMPLATE = "SELECT g1.dateTime, (g2.%(obs_type)s-g1.%(obs_type)s)/(g2.dateTime-g1.dateTime), " \
                   "g1.usUnits, g1.interval FROM archive g1 " \
                   "INNER JOIN archive g2 ON g2.dateTime=g1.dateTime-g1.interval*60 " \
                   "WHERE g1.dateTime>%(start)s AND g1.dateTime<=%(stop)s;"

    @staticmethod
    def get_series(obs_type, timespan, db_manager, aggregate_type=None, aggregate_interval=None):
        """Return a series from the database"""
        # We only know how to get series of power
        if not power_re.match(obs_type):
            raise weewx.UnknownType(obs_type)

        # Get the corresponding energy name
        energy_name = obs_type.replace('power', 'energy2')

        start_vec = list()
        stop_vec = list()
        data_vec = list()

        if aggregate_type:
            raise weewx.UnknownAggregation(aggregate_type)
        else:
            # No aggregation
            sql_str = BTExtends.SQL_TEMPLATE % {'obs_type': energy_name, 'start': timespan[0], 'stop': timespan[1]}
            std_unit_system = None
            for record in db_manager.genSql(sql_str):
                start_vec.append(record[0] - record[3] * 60)
                stop_vec.append(record[0])
                if std_unit_system:
                    if std_unit_system != record[2]:
                        raise weewx.UnsupportedFeature("Unit type cannot change within an aggregation interval.")
                else:
                    std_unit_system = record[2]
                data_vec.append(record[1])
            unit, unit_group = weewx.units.getStandardUnitType(std_unit_system, obs_type, aggregate_type)

        return (ValueTuple(start_vec, 'unix_epoch', 'group_time'),
                ValueTuple(stop_vec, 'unix_epoch', 'group_time'),
                ValueTuple(data_vec, unit, unit_group))

    @staticmethod
    def get_aggregate(obs_type, timespan, aggregate_type, db_manager, **option_dict):

        # We only know how to get aggregates of power
        if not power_re.match(obs_type):
            raise weewx.UnknownType(obs_type)
        # And, even then, only averages:
        if aggregate_type != 'avg':
            raise weewx.UnknownAggregation(aggregate_type)

        # Get the corresponding energy name
        energy_name = obs_type.replace('power', 'energy2')

        # The average power over the time period is just the time derivative of energy
        return weewx.xtypes.get_aggregate(energy_name, timespan, 'tderiv', db_manager, **option_dict)


class BrultechService(weewx.engine.StdService):
    """A WeeWX service that arranges for configuration information to be loaded. It also listens
    for NEW_LOOP_PACKET events, and uses them to fill out a packet with power information. """

    def __init__(self, engine, config_dict):
        # Initialize my base class
        super(BrultechService, self).__init__(engine, config_dict)

        # Start with the defaults. Make a copy --- we will be modifying it
        bt_config = configobj.ConfigObj(brultech_defaults)['Brultech']
        # Now merge in the overrides from the config file
        bt_config.merge(config_dict.get('Brultech', {}))

        # Create the specialized objects we will need:
        self.bt_accum_config = BTAccumConfig()
        self.bt_obs_group_dict = BTObsGroupDict()
        self.bt_extends = BTExtends(bt_config)

        # Now add them to their various services.

        # Add the specialized accumulator dictionary.
        weewx.accum.accum_dict.prepend(self.bt_accum_config)

        # Add the specialized dictionary for mapping type names to unit groups:
        weewx.units.obs_group_dict.extend(self.bt_obs_group_dict)

        # Add the extended types to the list of extensions
        weewx.xtypes.xtypes.append(self.bt_extends)

        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)

    def new_loop_packet(self, event):
        self.bt_extends.add_power_to_packet(event.packet)

    def shutDown(self):
        weewx.accum.accum_dict.remove(self.bt_accum_config)
        weewx.units.obs_group_dict.remove(self.bt_obs_group_dict)
        weewx.xtypes.xtypes.remove(BTExtends)
        self.bt_accum_config = None
        self.bt_obs_group_dict = None
        self.bt_extends = None


if __name__ == '__main__':
    import optparse
    import weeutil.logger
    from weeutil.weeutil import to_sorted_string

    weewx.debug = 2

    weeutil.logger.setup('brultech', {})

    usage = """Usage: python -m brultech --help
       python -m brultech --version
       python -m brultech [--host=HOST] [--port=PORT]"""

    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--version', action='store_true',
                      help='Display driver version')
    parser.add_option('--host', default='192.168.1.7',
                      help='Host. Default is "192.168.1.7"',
                      metavar="HOST")
    parser.add_option('--port', type="int", default=8083,
                      help='Serial port to use. Default is "8083"',
                      metavar="PORT")
    (options, args) = parser.parse_args()

    if options.version:
        print("Brultech driver version %s" % DRIVER_VERSION)
        exit(0)

    print("Using host '%s' on port %d" % (options.host, options.port))

    config_dict = {
        'Brultech': {
            'connection': 'socket',
            'socket': {
                'host': options.host,
                'port': options.port
            }
        }
    }

    device = loader(config_dict, None)
    device.setTime()

    for pkt in device.genLoopPackets():
        print(to_sorted_string(pkt))
