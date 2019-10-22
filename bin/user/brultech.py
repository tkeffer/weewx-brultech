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
from weeutil.weeutil import to_int, to_float

log=logging.getLogger(__name__)

DRIVER_NAME = 'BRULTECH'
DRIVER_VERSION = '0.1.0'

DEFAULTS_INI = u"""
[Brultech]

    # See the install instructions for how to configure the Brultech devices!!
    
    # How often to poll the device for data
    poll_interval = 5
    
    # The type of packet to use:
    packet_type = GEM48PDBinary
    
    # The type of connection to use. Right now, only 'socket' is supported.
    connection = socket
    
    # The following is for socket connections: 
    socket_host = 192.168.1.101
    socket_port = 8086
    
    # What units the temperature sensors will be in:
    temperature_unit = degree_F

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

    def write(self, data):
        raise NotImplementedError()

    def write_with_response(self, prompt, expected, max_tries=3):
        """Write a prompt, then wait for an expected response"""
        for i_try in range(max_tries):
            self.write(prompt)
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

    def __init__(self, socket_host, socket_port, socket_timeout=20):
        """Initialize a SocketConnection.

        NAMED ARGUMENTS:

        socket_host: IP host. No default.

        socket_port: The socket to be used. No default.

        socket_timeout: How long to wait on a socket connection. Default is 20 seconds.
        """
        super(SocketConnection, self).__init__()
        import socket

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(socket_timeout)
            self.socket.connect((socket_host, socket_port))
        except (socket.error, socket.herror, socket.gaierror, socket.timeout) as ex:
            log.error("Error while opening socket to host %s on port %d: %s", socket_host, socket_port, ex)
            # Reraise as a WeeWX error:
            raise weewx.WeeWxIOError(ex)
        log.debug("Opened up socket to host %s on port %d", socket_host, socket_port)

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
        remaining = chars
        while remaining:
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
#                            Packet Utilities
# ===============================================================================

def unpack(a):
    """Unpack a value from a byte array, little endian order."""
    s = reduce(lambda s, x: s + x[1] * (1 << (8 * x[0])), enumerate(a), 0)
    return s


def extract_short(buf):
    v = (buf[0] << 8) + buf[1]
    return v


def extract_seq(buf, N, nbyte, tag):
    results = {}
    for i in range(N):
        x = unpack(buf[i * nbyte:i * nbyte + nbyte])
        results[tag % (i + 1)] = x
    return results


# ===============================================================================
#                           Packet Drivers
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
        """Set up the source to generate the requested packet type.

        source: An object with member function
          write_with_response(prompt, expected_response)"""

        self.source.write_with_response(b"^^^SYSPKT%02d" % self.packet_format, b"PKT\r\n")

    def get_packet(self):
        # Get a bytebuffer...
        byte_buf = self.source.read(self.packet_length)

        # ... then check it for integrity:
        self._check_ends(byte_buf)
        self._check_checksum(byte_buf)
        self._check_ID(byte_buf)

        # ... then extract the packet from the buffer
        packet = self.extract_packet_from(byte_buf)
        return packet

    def extract_packet_from(self, byte_buf):
        """Should be provided by a specializing class."""
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

    # Adapted from btmon.py:
    @staticmethod
    def _calc_secs(oldpkt, newpkt):
        ds = newpkt['secs'] - oldpkt['secs']
        if newpkt['secs'] < oldpkt['secs']:
            ds += BTBase.SEC_COUNTER_MAX
        return ds


class GEMBin48Net(BTBase):
    """Implement the GEM BIN48-NET (format #5) packet"""

    def __init__(self, **bt_dict):
        super(GEMBin48Net, self).__init__(**bt_dict)
        self.packet_length = 619
        self.packet_ID = 5
        self.packet_format = 5
        self.NUM_CHAN = 32  # there are 48 channels, but only 32 usable

    def extract_packet_from(self, byte_buf):
        packet = {
            'packet_id': self.packet_ID,
            'dateTime': int(time.time() + 0.5),
            }

        # Extract volts:
        packet['volts'] = float(extract_short(byte_buf[3:5])) / 10.0

        # Extract absolute watt-seconds:
        aws = extract_seq(byte_buf[5:], 32, 5, 'ch%d_aws')
        packet.update(aws)

        # Extract polarized watt-seconds:
        pws = extract_seq(byte_buf[245:], 32, 5, 'ch%d_pws')
        packet.update(pws)

        # Extract serial number:
        packet['ser_no'] = extract_short(byte_buf[485:487])

        # Extract device ID:
        packet['unit_id'] = byte_buf[488]

        # Form the full, formatted serial number:
        packet['serial'] = '%03d%05d' % (packet['unit_id'], packet['ser_no'])

        # Extract seconds from 3 bytes:
        packet['secs'] = unpack(byte_buf[585:588])

        # Extract pulses:
        pulse = extract_seq(byte_buf[588:], 4, 3, 'pulse%d')
        packet.update(pulse)

        # Extract temperatures:
        temperature = extract_seq(byte_buf[600:], 8, 2, 'extraTemp%d')
        packet.update(temperature)

        return packet


class GEMBin48NetTime(GEMBin48Net):
    """Implement the GEM BIN48-NET-TIME (format #4) packet"""

    def __init__(self, **bt_dict):
        super(GEMBin48NetTime, self).__init__(**bt_dict)
        self.packet_length = 625
        self.packet_format = 4

    def extract_packet_from(self, byte_buf):
        # Get the packet from my superclass
        packet = GEMBin48Net.extract_packet_from(self, byte_buf)

        # Add the embedded time:
        Y, M, D, h, m, s = byte_buf[616:622]
        time_tt = (Y + 2000, M, D, h, m, s, 0, 0, -1)
        packet['time_created'] = int(calendar.timegm(time_tt))

        return packet


# ===============================================================================
#                                The driver
# ===============================================================================

class Brultech(object):

    def __init__(self, source, **bt_dict):
        """Initialize using a byte source.

        If the device has not been initialized to start sending packets by some
        other means, this can be done by calling method setup().

        Parameters:
        source: The source of the bytes. Should have methods read() and write().

        NAMED ARGUMENTS:

        PacketClass: The class for the expected packet type. Default is GEM48PDBinary.

        max_tries: The maximum number of tries that should be attempted in the event
        of a read error. Default is 3.

        poll_interval: How often to poll for data. Default is 5.

        OTHER ARGUMENTS:

        connection: Type of connection. Default is 'socket'

        For socket connections:
        socket_host: The IP address or host name of the monitor

        socket_port: Its port
        """

        self.source = source
        self.max_tries = to_int(bt_dict.get('max_tries', 3))
        self.poll_interval = to_int(bt_dict.get('poll_interval', 5))
        # Get the class of the packet type to use:
        PacketClass = bt_dict.get('packet_type', GEMBin48NetTime)
        # Now instantiate an instance:
        self.packet_driver = PacketClass(**bt_dict)
        self.setup()

    def setup(self):
        """Initialize the GEM to start sending packets.

        Parameters:
        send_interval: The time in seconds between packets. Default is 5."""

        self.source.flush_input()
        self.source.write_with_response(b"^^^SYSOFF", "bOFF\r\n")
        self.source.write_with_response(b"^^^SYSIVL%03d" % send_interval, b"IVL\r\n")
        self.packet_driver.setup(self.source)
        self.source.write_with_response(b"^^^SYS_ON", b"_ON\r\n")

    def genLoopPackets(self):
        """Generator function that returns packets."""

        while True:
            packet = self.packet_driver.get_packet_from(self.source)
            yield packet


if __name__ == '__main__':

    import weeutil.weeutil

    source = SocketConnection.open(host='GEMCAT', tcp_port=8083)

    gem_ = Brultech(source)
    gem_.setup()

    for ipacket, packet in enumerate(gem_.genLoopPackets()):
        print(ipacket, weeutil.weeutil.timestamp_to_string(packet['time_created']), packet['volts'])

        for key in packet:
            if key.startswith('ch8'):
                print(" %s = %s," % (key, packet[key]))
