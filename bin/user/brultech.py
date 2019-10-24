#
#    Copyright (c) 2013-2019 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""Classes and functions for interfacing with the Brultech energy monitors.

A simplifying assumption is that the Brultech device is always in server mode,
and the driver only polls it. See README.md for instructions for setting up the device.

From Matthew Wall: these are 'hard data', i.e. things we get from the device:

    secs - seconds counter from the gem.  Monotonically increasing counter.  Wraparound at 256^3.  Increments once per second.
    aws - absolute watt-seconds.  Monotonically increasing counter.  Wraparound at 256^5.
    pws - polarized watt-seconds.  Monotonically increasing counter.  Wraparound at 256^5.  pws is always less than or equal to aws.
    volts - voltage
    t - temperature in degrees C

There is no notion of None.  We can infer it for t if we get a value of 255.5.  For other channels there is no way to know whether a CT or pulse counter is attached.
"""
from __future__ import print_function, with_statement

import calendar
import logging
import time
from functools import reduce

try:
    # Python 3
    import io
except ImportError:
    # Python 2
    import StringIO as io

import configobj

import weewx
import weewx.drivers
from weeutil.weeutil import to_int, to_float

log = logging.getLogger(__name__)

DRIVER_NAME = 'BRULTECH'
DRIVER_VERSION = '0.1.0'

DEFAULTS_INI = u"""
[Brultech]

    # See the install instructions for how to configure the Brultech devices!!
    
    # The type of packet to use:
    packet_type = GEMBin48NetTime
    
    # The type of connection to use. It should match a section below. 
    # Right now, only 'socket' is supported.
    connection = socket
    
    # How often to poll the device for data
    poll_interval = 5

    # Max number of times to try an I/O operation before declaring an error
    max_tries = 3
    
    # What units the temperature sensors will be in:
    temperature_unit = degree_F

    [[socket]]
        # The following is for socket connections: 
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
    """Base type for various Brultech packet types.

    Superclasses should provide:

    self.packet_format: The packet format number. See the Brultech Packet Format Guide

    self.packet_ID: Not to be confused with "packet_format" above. This is the byte
    identifier carried by the packet in index 2.

    self.packet_length: The length of the packet.
    """

    SEC_COUNTER_MAX = 16777216  # 256^3
    BYTE4_COUNTER_MAX = 4294967296  # 256^4
    BYTE5_COUNTER_MAX = 1099511627776  # 256^5

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


class GEMBin48Net(BTBase):
    """Implement the GEM BIN48-NET (format #5) packet"""

    def __init__(self, source, **bt_dict):
        super(GEMBin48Net, self).__init__(source)
        self.packet_length = 619
        self.packet_ID = 5
        self.packet_format = 5
        self.NUM_CHAN = 32  # there are 48 channels, but only 32 usable

    def _extract_packet_from(self, byte_buf):
        packet = {
            'dateTime': int(time.time() + 0.5),
            'usUnits': weewx.METRICWX,
            'volts': float(extract_short(byte_buf[3:5])) / 10.0,
            'ser_no': extract_short(byte_buf[485:487]),
            'unit_id': byte_buf[488],
            'secs': unpack(byte_buf[585:588])
            }

        # Extract absolute watt-seconds:
        aws = extract_seq(byte_buf[5:], 32, 5, 'ch%d_a_energy')
        packet.update(aws)

        # Extract polarized watt-seconds:
        pws = extract_seq(byte_buf[245:], 32, 5, 'ch%d_p_energy')
        packet.update(pws)

        # Form the full, formatted serial number:
        packet['serial'] = '%03d%05d' % (packet['unit_id'], packet['ser_no'])

        # Extract pulses:
        pulse = extract_seq(byte_buf[588:], 4, 3, 'p%d_count')
        packet.update(pulse)

        # Extract temperatures:
        for i in range(8):
            t = _mktemperature(byte_buf[600 + 2 * i:])
            # Ignore any out of range temperatures; keep the rest
            if abs(t) <= 255:
                packet['t%d_temperature' % (i + 1)] = t

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
        # This sets the temperature units to Celsius. Not sure about response on this one...
        self.source.write_with_response(b'^^^TMPDGC', b'OK\r\n')
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
            time.sleep(self.poll_interval)

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
        self.source.write_with_response(b"^^^SYSDTM%b\r" % time_str, b"DTM\r\n")


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
#                            Packet Utilities
# ===============================================================================

def unpack(a):
    """Unpack a value from a byte array, little endian order."""
    sum = 0
    for b in a[::-1]:
        sum <<= 8
        sum += b
    return sum


def extract_short(buf):
    v = (buf[0] << 8) + buf[1]
    return v


def extract_seq(buf, N, nbyte, tag):
    results = {}
    for i in range(N):
        x = unpack(buf[i * nbyte:i * nbyte + nbyte])
        results[tag % (i + 1)] = x
    return results


# Adapted from btmon.py
def _mktemperature(b):
    # firmware 1.61 and older use this for temperature
    #        t = 0.5 * self._convert(b)

    # firmware later than 1.61 uses this for temperature
    t = 0.5 * ((b[1] & 0x7f) << 8 | b[0])
    if (b[1] >> 7) != 0:
        t = -t
    return t


# Adapted from btmon.py:
def _calc_secs(oldpkt, newpkt):
    ds = newpkt['secs'] - oldpkt['secs']
    if newpkt['secs'] < oldpkt['secs']:
        ds += BTBase.SEC_COUNTER_MAX
    return ds


if __name__ == '__main__':
    from weeutil.weeutil import timestamp_to_string
    import weeutil.logger

    weewx.debug = 2

    weeutil.logger.setup('brultech', {})

    device = loader({}, None)
    device.setTime()

    for packet in device.genLoopPackets():
        print(timestamp_to_string(packet['dateTime']), timestamp_to_string(packet['time_created']), packet['volts'])
        #
        # for key in packet:
        #     if key.startswith('ch8'):
        #         print(" %s = %s," % (key, packet[key]))
