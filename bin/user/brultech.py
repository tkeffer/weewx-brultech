#
#    Copyright (c) 2013-2022 Tom Keffer <tkeffer@gmail.com>
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

import weedb
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
DRIVER_VERSION = '2.1.0'

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
del io


def loader(config_dict, engine):
    # Start with the defaults. Make a copy --- we will be modifying it
    bt_config = configobj.ConfigObj(brultech_defaults)['Brultech']
    # Now merge in the overrides from the config file
    bt_config.merge(config_dict.get('Brultech', {}))
    # Instantiate and return the driver
    return Brultech(**bt_config.dict())


def configurator_loader(config_dict):  # @UnusedVariable
    return BrultechConfigurator()


def confeditor_loader():
    return BrultechConfEditor()


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

        Args:
            host (str): IP host. No default.
            port (int): The socket to be used. No default.
            send_delay (float): After sending a command, how long to wait before looking for a response.
            timeout (float: How long to wait on a socket connection. Default is 20 seconds.
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
            'dateTime': time.time(),
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
        # When the device starts for the first time, it will not have a valid
        # time, causing a ValueError. Be prepared to catch it.
        try:
            packet['time_created'] = int(calendar.timegm(time_tt))
        except ValueError:
            # Return zero, which will force a clock synchronization.
            packet['time_created'] = 0

        return packet


# ===============================================================================
#                                The driver
# ===============================================================================

class Brultech(weewx.drivers.AbstractDevice):

    def __init__(self, **bt_dict):
        """Initialize from a config dictionary

        Args:
            packet_type (str): The class for the expected packet type. Default is 'GEMBin48NetTime'.
            connection (str): Type of connection. Default is 'socket'
            poll_interval (int): How often to poll for data. Default is 5.
            max_tries(int): The maximum number of tries that should be attempted in the event
                of a read error. Default is 3.

        # In addition, there should be a subdictionary with key matching 'connection'
        above. For example, for sockets:
            socket (dict): { 'host':  192.168.1.101, 'port': 8086, 'timeout': 20 }
        """

        self.poll_interval = to_float(bt_dict.get('poll_interval', 5))
        self.max_tries = to_int(bt_dict.get('max_tries', 3))
        self.max_channels = to_int(bt_dict.get('max_channels', 32))
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

    def setTime(self, t=None):
        """Set the time to t"""

        t = t or time.time()
        # Unfortunately, clock resolution is only 1 second, and transmission takes a
        # little while to complete, so round up the clock up. 0.5 for clock resolution
        # and 0.25 for transmission delay. Also, the Brultech uses UTC.
        newtime_tt = time.gmtime(int(t + 0.75))
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
        """ Typical response through byte 124:
            bytearray(b'ALL\r\n00,                             # Byte 0; Spare
              40,40,40,40,40,40,C0,40,40,40,40,40,40,40,40,40, # Channel options ch 01-16
              40,40,40,40,40,40,40,40,40,40,40,40,40,40,40,40, # Channel options ch 17-32
              00,00,00,00,00,00,00,00,00,00,00,00,00,00,00,00, # Channel options ch 33-48
              D3,D4,D3,D3,D3,D2,92,90,D3,D3,D3,D3,D3,D3,D3,D3, # CT type ch 01-16
              D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3, # CT type ch 17-32
              D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3,D3, # CT type ch 33-48
              34,44,44,23,44,44,44,44,44,44,44,44,             # CT range ch 01-24 (one nibble per channel)
              44,44,44,44,44,44,44,44,44,44,44,44,             # CT range ch 25-48 (one nibble per channel)
              BB,03,                                           # PT type, PT range
              04,05,                                           # Packet format, packet send interval
              ...
              """
        response = self.source.read_with_prompt(b'^^^RQSALL', None)
        return response


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
        """Query the configuration of the Brultech, printing out status
        information.

        CT-model   CT-type   CT-range
         M-40        211      4
         M-50        210      4
         M-80        210      4
         M-100       212      3
         S-30        205      4
         S-60        180      4
         S-100       146      3
         S-200       144      2
         S-400       146      1

         Note how model M-50 and M-80 have the same CT-type and range. They cannot be distinguished from one another.
         Models S-100 and S-400 have the same CT-type, but different ranges. They can be differentiated this way.
        """

        CT_STR = {
            180: "S-60",
            205: "S-30",
            210: "M-50/M-80",
            211: "M-40",
            212: "M-100",
            144: "S-200",
            146: "S-100"
        }
        print("Querying...")
        info = device.get_info()
        # First split on commas to get little bytearrays with the ascii values.
        # Start at index 5 to get around the leading b"ALL\r\n"
        val_bytelist = info[5:].split(b',')
        # Now convert to ints, using base 16. The result will be a list of byte values
        val_buf = [int(v, 16) for v in val_bytelist]

        print("     Standard/           CT-        CT-   CT-")
        print("Ch   Net        Polarity model      type  range")
        for channel in range(1, device.max_channels + 1):
            channel_options = val_buf[channel]
            net_metering = 'Standard' if 0x40 & channel_options else 'Net'
            polarity = '-' if bool(0x80 & channel_options) else '+'
            ct_type = val_buf[48 + channel]
            range_offset = channel // 2 + 97
            range_nibble = channel % 2
            ct_range = val_buf[range_offset] & 0x0f if range_nibble else (val_buf[range_offset] & 0xf0) >> 4
            ct_model = CT_STR.get(ct_type, "Unk")
            if ct_type == 146 and ct_range == 1:
                ct_model = "S-400"
            s = "%2d %10s   %1s        %-9s%5d %3d" % (channel, net_metering, polarity, ct_model, ct_type, ct_range)
            print(s, file=dest)

        pt_type = val_buf[121]
        pt_range = val_buf[122]
        packet_format = val_buf[123]
        packet_interval = val_buf[124]
        print("             PT-type: %d" % pt_type, file=dest)
        print("            PT-range: %d" % pt_range, file=dest)
        print("       Packet format: %d" %packet_format, file=dest)
        print("Packet send interval: %ds" % packet_interval, file=dest)


# =============================================================================
#                      Class BrultechConfEditor
# =============================================================================

class BrultechConfEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return DEFAULTS_INI

    def prompt_for_settings(self):
        settings = self.existing_options
        print("Specify the IP address (e.g., 192.168.0.10) or hostname of the Brultech monitor.")
        try:
            default_host = self.existing_options['socket']['host']
        except KeyError:
            default_host = brultech_defaults['Brultech']['socket']['host']
        host = self._prompt('host', default_host)
        print("Specify the port")
        try:
            default_port = self.existing_options['socket']['port']
        except KeyError:
            default_port = brultech_defaults['Brultech']['socket']['port']
        port = self._prompt('port', default_port)
        if 'socket' not in settings:
            settings['socket'] = brultech_defaults['Brultech']['socket'].dict()
        settings['socket']['host'] = host
        settings['socket']['port'] = port
        return settings


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
denergy2_re = re.compile(r'^ch[0-9]+(_[ap]d)?_energy2$')
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
        global volt_re, temperature_re, energy2_re, denergy2_re, count_re
        if volt_re.match(key) or temperature_re.match(key):
            # These are intensive quantities. The defaults will do.
            return weewx.accum.OBS_DEFAULTS
        elif energy2_re.match(key) or count_re.match(key) \
                or key in ('time_created', 'secs', 'unit_id'):
            # These are extensive quantities. We need the last value (rather than the average).
            return {'extractor': 'last'}
        elif denergy2_re.match(key):
            # Delta energies are like rain: they have to be summed
            return {'extractor': 'sum'}
        elif key == 'ser_no' or key == 'serial':
            # These are strings
            return {'accumulator': 'firstlast', 'extractor': 'last'}
        else:
            # Don't know what it is. Raise a KeyError
            raise KeyError(key)

    def __contains__(self, key):
        """Does key match any type we know about?"""
        global volt_re, temperature_re, energy2_re, denergy2_re, count_re
        return key in ('time_created', 'secs', 'unit_id', 'ser_no', 'serial') \
               or volt_re.match(key) \
               or temperature_re.match(key) \
               or energy2_re.match(key) \
               or denergy2_re.match(key) \
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
        global volt_re, temperature_re, energy2_re, denergy2_re, count_re, power_re
        if key == 'time_created':
            return "group_time"
        elif key == 'secs':
            return "group_deltatime"
        elif volt_re.match(key) \
                or temperature_re.match(key) \
                or energy2_re.match(key) \
                or denergy2_re.match(key) \
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
        global volt_re, temperature_re, energy2_re, denergy2_re, count_re, power_re
        return key == 'time_created' \
               or key == 'secs' \
               or volt_re.match(key) \
               or temperature_re.match(key) \
               or energy2_re.match(key) \
               or denergy2_re.match(key) \
               or count_re.match(key) \
               or power_re.match(key)


class BTExtends(weewx.xtypes.XType):

    def __init__(self, bt_dict):
        self.stale = to_int(bt_dict.get('stale', 1800))
        self.prev_record = None

    def get_scalar(self, obs_type, record, db_manager, **option_dict):
        """Calculate a Brultech derived observation type.

        This version only knows how to calculate power from delta energy.
        """

        if not power_re.match(obs_type):
            # Don't know what it is.
            raise weewx.UnknownType(obs_type)

        # Get the corresponding "denergy2" name from the power name.
        # This replaces something like ch5_a_power with ch5_ad_energy2:
        denergy2_name = obs_type.replace('_power', 'd_energy2')

        # We require that both "interval" and the delta energy value be in the record:
        if not record or "interval" not in record or denergy2_name not in record:
            raise weewx.CannotCalculate(obs_type)

        if record[denergy2_name] is not None:
            val = record[denergy2_name] / (record['interval'] * 60)
        else:
            val = None

        # Figure out the unit and group of the desired observation type. Most likely, the result will
        # be 'watt', 'group_power', but an explicit call makes this more future-proof.
        unit, group = weewx.units.getStandardUnitType(record['usUnits'], obs_type)

        return ValueTuple(val, unit, group)

    SQL_SERIES_TEMPLATE = "SELECT dateTime, %(obs_type)s / (%(`interval`)s * 60.0), usUnits, `interval` " \
                          "FROM %(table)s WHERE dateTime > %(start)s AND dateTime <= %(stop)s;"

    @staticmethod
    def get_series(obs_type, timespan, db_manager, aggregate_type=None, aggregate_interval=None, **option_dict):
        """Return a series from the database.

        This version only knows how to calculate a series of power from energy2 with no aggregation.
        """

        # TODO: Allowing aggregation would make this whole thing much more efficient.

        if not power_re.match(obs_type):
            raise weewx.UnknownType(obs_type)

        # Get the corresponding delta energy name from the power name. This replaces something like ch5_a_power
        # with ch5_ad_energy2:
        energy2_name = obs_type.replace('_power', 'd_energy2')

        start_vec = list()
        stop_vec = list()
        data_vec = list()

        if aggregate_type:
            raise weewx.UnknownAggregation(aggregate_type)
        else:
            # No aggregation
            sql_str = BTExtends.SQL_SERIES_TEMPLATE % {'obs_type': energy2_name, 'table': db_manager.table_name,
                                                       'start': timespan[0], 'stop': timespan[1]}
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

    SQL_AGG_TEMPLATE = "SELECT SUM(%(energy_name)s) / (SUM(`interval`) * 60), MIN(usUnits), MAX(usUnits) " \
                       "FROM %(table_name)s " \
                       "WHERE dateTime > %(start)s AND dateTime <= %(stop)s " \
                       "AND %(energy_name)s IS NOT NULL;"

    @staticmethod
    def get_aggregate(obs_type, timespan, aggregate_type, db_manager, **option_dict):
        """Calculate average power.

        This version only knows how to calculate average power from delta energy.
        """

        # We only know how to get aggregates of power
        if not power_re.match(obs_type):
            raise weewx.UnknownType(obs_type)
        # And, even then, only averages:
        if aggregate_type != 'avg':
            raise weewx.UnknownAggregation(aggregate_type)

        # Get the corresponding energy name
        energy_name = obs_type.replace('_power', 'd_energy2')

        # Form the interpolation dictionary
        interpolate_dict = {
            'energy_name': energy_name,
            'table_name': db_manager.table_name,
            'start': timespan.start,
            'stop': timespan.stop
        }

        select_stmt = BTExtends.SQL_AGG_TEMPLATE % interpolate_dict

        try:
            row = db_manager.getSql(select_stmt)
        except weedb.NoColumnError:
            raise weewx.UnknownType(aggregate_type)

        if row is None or row[0] is None:
            value = None
        else:
            if not (row[1] == row[2] == db_manager.std_unit_system):
                raise weewx.UnsupportedFeature("Mixed unit systems")
            value = row[0]

        if db_manager.std_unit_system not in weewx.units.std_groups:
            raise weewx.UnsupportedFeature("Brultech driver only support US, METRIC, and METRICWX unit systems")

        return ValueTuple(value, "watt", "group_power")


class BrultechService(weewx.engine.StdService):
    """A WeeWX service that arranges for configuration information to be loaded. It also listens
    for NEW_LOOP_PACKET events, and uses them to fill out a packet with power information. """

    def __init__(self, engine, config_dict):
        # Initialize my base class
        super(BrultechService, self).__init__(engine, config_dict)

        self.prev_packet = None
        self.prev_record = None

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
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def new_loop_packet(self, event):
        augment_record(event.packet, self.prev_packet)
        self.prev_packet = event.packet

    def new_archive_record(self, event):
        augment_record(event.record, self.prev_record)
        self.prev_record = event.record

    def shutDown(self):
        """Remove the extensions that were added by __init__()"""
        weewx.accum.accum_dict.maps.remove(self.bt_accum_config)
        weewx.units.obs_group_dict.maps.remove(self.bt_obs_group_dict)
        weewx.xtypes.xtypes.remove(self.bt_extends)
        self.bt_accum_config = None
        self.bt_obs_group_dict = None
        self.bt_extends = None


def augment_record(record, prev_record):
    global energy2_re
    if prev_record:
        # Scan through the record... (We will be changing its size, so we need to scan through
        # a static list to avoid Python 3 errors).
        for obs_type in list(record.keys()):
            # ... looking for energy keys that we recognize.
            if energy2_re.match(obs_type):
                # Found one. Use it to calculate power and delta energies
                power_name = obs_type.replace('_energy2', '_power')
                delta_name = obs_type.replace('_energy2', 'd_energy2')
                # Be sure to convert to whatever unit system is in use in the record, then store
                # just the ".value" part
                if power_name not in record:
                    record[power_name] = weewx.units.convertStd(calc_power(power_name, record, prev_record),
                                                                record['usUnits']).value
                if delta_name not in record:
                    record[delta_name] = weewx.units.convertStd(calc_delta_energy(delta_name, record, prev_record),
                                                                record['usUnits']).value


def calc_power(power_name, record, prev_record):
    """Given energy2, calculate power, which is just its time derivative.

    Args:
        power_name (str): The name of the desired power. Something like ch2_a_power
        record (dict): A dictionary of types, holding present values.
        prev_record (dict): A dictionary of types, holding previous values.

    Returns:
        ValueTuple: The time derivative of energy2
    """
    # Get the name of the field with accumulated energy
    energy2_name = power_name.replace('_power', '_energy2')

    if record[energy2_name] is not None \
            and prev_record \
            and prev_record.get(energy2_name) is not None:
        deriv = (record[energy2_name] - prev_record.get(energy2_name)) \
                / (record['dateTime'] - prev_record['dateTime'])
    else:
        deriv = None
    unit, unit_group = weewx.units.getStandardUnitType(record['usUnits'], power_name)

    return ValueTuple(deriv, unit, unit_group)


def calc_delta_energy(delta_name, record, prev_record):
    """Given energy2, calculate the change in energy from the last record

    Args:
        delta_name (str): The name of the desired delta energy observation type. Something like ch2_ad_energy2
        record (dict): A dictionary of types, holding present values.
        prev_record (dict): A dictionary of types, holding previous values.

    Returns:
        ValueTuple: The difference of energy2
    """
    # Get the name of the accumulated energy
    energy2_name = delta_name.replace('d_energy2', '_energy2')

    if record[energy2_name] is not None \
            and prev_record \
            and prev_record.get(energy2_name) is not None:
        val = record[energy2_name] - prev_record.get(energy2_name)
    else:
        val = None
    unit, unit_group = weewx.units.getStandardUnitType(record['usUnits'], delta_name)

    return ValueTuple(val, unit, unit_group)


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
