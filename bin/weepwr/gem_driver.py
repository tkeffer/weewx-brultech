#
#    Copyright (c) 2013 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""Classes and functions for interfacing with the Brultech GEM home energy monitor.

What follows is from notes from Matthew Wall:

These are 'hard data', i.e. things we get from the device:

    secs - seconds counter from the gem.  Monotonically increasing counter.  Wraparound at 256^3.  Increments once per second.
    aws - absolute watt-seconds.  Monotonically increasing counter.  Wraparound at 256^5.
    pws - polarized watt-seconds.  Monotonically increasing counter.  Wraparound at 256^5.  pws is always less than or equal to aws.
    volts - voltage
    t - temperature in degrees C

There is no notion of None.  We can infer it for t if we get a value of 255.5.  For other channels there is no way to know whether a CT or pulse counter is attached.

These are all derived quantities:

    w - net watts.  Calculated from packets n and n-1.
    wh - cumulative net watt-hours.  Calculated from packet n.
    dwh - delta watt-hours.  Difference between reading n and n-1.
    nw - negative watts.  Power generated.  Calculated from packets n and n-1.
    pw - positive watts.  Power consumed.  Calculated form packets n and n-1.
    nwh - negative watt-hours.  Energy generated.
    pwh - positive watt-hours.  Energy consumed.

Note that the meaning of positive/negative depend on:
- orientation of the ct
- per-channel polarity setting on the gem
- white/black wire positions in the gem wiring connectors

These are the calculations:

    seconds = sec_counter1 - sec_counter0
    w1 = (abs_ws1 - abs_ws0) / seconds    # if pos_ws1 == 0 multiply by -1
    pos_w1 = (pol_ws1 - pol_ws0) / seconds
    neg_w1 = ((abs_ws1 - abs_ws0) - (pol_ws1 - pol_ws0)) / seconds
    pos_wh1 = pol_ws1 / 3600
    neg_wh1 = (abs_ws1 - pol_ws1) / 3600
    wh1 = pos_wh1 - neg_wh1               # same as (2*pws1 - aws1) / 3600
    delta_wh1 = wh1 - wh0

When polarity is reversed, use the following:

    neg_wh1 = pol_ws1 / 3600
    pos_wh1 = (abs_ws1 - pol_ws1) / 3600
    wh1 = (aws1 - 2*pws1) / 3600
"""
import calendar
import time
try:
    import syslog
except ImportError:
    # If running on a non-Unix machine, this provides a simple alternative that
    # prints to stderr
    import sys
    class syslog(object):
        LOG_DEBUG   = "DEBUG"
        LOG_ERR     = "ERROR"
        LOG_WARNING = "WARNING"
        @staticmethod
        def syslog(severity, msg):
            print >>sys.stderr, "Severity: %s; '%s'" % (severity, msg)

import weepwr

#===============================================================================
#                           Connection Classes    
#===============================================================================

class BaseConnection(object):
    """Abstract base class representing a connection."""
    
    def __init__(self, tcp_send_delay=0.5):
        self.tcp_send_delay = tcp_send_delay
        
    def close(self):
        pass

    def flush_input(self):
        pass
    
    def flush_output(self):
        pass

    def queued_bytes(self):
        pass
    
    def read(self, chars=1, max_tries=3):
        pass
    
    def write(self, data):
        pass
        
    def write_with_response(self, prompt, expected, max_tries=3):
        "Write a prompt, then wait for an expected response"
        for i_try in xrange(max_tries):
            self.write(prompt)
            time.sleep(self.tcp_send_delay)
            length   = self.queued_bytes()
            response = self.read(length)
            print "For prompt", prompt, " got", response.encode('string_escape')
            if response == expected:
                return
            syslog.syslog(syslog.LOG_DEBUG, "GEM: Try %d, for prompt %s" % (i_try+1, prompt))
        else:
            syslog.syslog(syslog.LOG_ERR, "GEM: Prompt %s, max tries (%d) exceeded" % (prompt, max_tries))
            raise weepwr.ResponseError("Prompt %s, max tries (%d) exceeded" % (prompt, max_tries))
    
class SocketConnection(BaseConnection):
    """Wraps a socket connection, supplying simplified access methods."""
    
    def __init__(self, socket, tcp_send_delay=0.5):
        super(SocketConnection, self).__init__(tcp_send_delay)
        self.socket = socket
        
    @staticmethod
    def open(**gem_dict):
        """Open a socket on a specified host and tcp/ip port and return it.
        
        NAMED ARGUMENTS:
        
        host: IP host. No default.
        
        tcp_port: The socket to be used. No default.
        
        timeout: How long to wait on a TCP connection. Default is 20 seconds.
        
        tcp_send_delay: How long to wait after sending an instruction, before reading
        the response. Default is 0.5 seconds
        """
        import socket
        
        # Unpack the arguments:
        host     = gem_dict['host']
        tcp_port = int(gem_dict['tcp_port'])
        timeout  = float(gem_dict.get('timeout', 20))
        tcp_send_delay = float(gem_dict.get('tcp_send_delay', 0.5))
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, tcp_port))
        except (socket.error, socket.timeout, socket.herror), ex:
            syslog.syslog(syslog.LOG_ERR, "GEM: Socket error while opening tcp/ip port %d to ethernet host %s." % (tcp_port, host))
            # Reraise as a GEM I/O error:
            raise weepwr.WeePwrIOError(ex)
        except:
            syslog.syslog(syslog.LOG_ERR, "GEM: Unable to connect to ethernet host %s on tcp/ip port %d." % (host, tcp_port))
            raise
        syslog.syslog(syslog.LOG_DEBUG, "GEM: Opened up ethernet host %s on tcp/ip port %d" % (host, tcp_port))
        return SocketConnection(sock, tcp_send_delay)

    def close(self):
        import socket
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
        except Exception:
            pass

    def flush_input(self):
        """Flush the input buffer"""
        try:
            # This is a bit of a hack, but there is no analogue to pyserial's flushInput()
            old_timeout=self.socket.gettimeout()
            self.socket.settimeout(0)
            # Read a bunch of bytes, but throw them away.
            self.socket.recv(4096)
        except Exception:
            pass
        finally:
            self.socket.settimeout(old_timeout)

    def flush_output(self):
        """Flush the output buffer

        This function does nothing as there should never be anything left in
        the buffer when using socket.sendall()"""
        pass

    def queued_bytes(self):
        """Determine how many bytes are in the buffer"""
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
        """Read bytes"""
        import socket
        _try = 1
        _buffer = ''
        _remaining = chars
        while _remaining:
            if _try > 1:
                syslog.syslog(syslog.LOG_DEBUG, "Try #%d, requested %d characters, got %d" % (_try-1, chars, len(_buffer)))
            if _try > max_tries:
                raise weepwr.WeePwrIOError("Requested %d characters, only %d received" % (chars, len(_buffer)))
            try:
                _recv = self.socket.recv(_remaining)
            except (socket.timeout, socket.error), ex:
                # Reraise as a GEM I/O error:
                raise weepwr.WeePwrIOError(ex)
            _buffer += _recv
            _remaining -= len(_buffer)
            _try += 1
        return _buffer
    
    def write(self, data):
        """Write bytes"""
        import socket
        try:
            self.socket.sendall(data)
        except (socket.timeout, socket.error), ex:
            syslog.syslog(syslog.LOG_ERR, "GEM: Socket write error.")
            # Reraise as a GEM I/O error:
            raise weepwr.WeePwrIOError(ex)
        
#===============================================================================
#                            Packet Utilities
#===============================================================================

def unpack(a):
    """Unpack a value from a byte array, little endian order."""
    s = reduce(lambda s, x: s + x[1]*(1<<(8*x[0])), enumerate(a), 0)
    return s
            
def extract_short(buf):
    v = (buf[0] << 8) + buf[1]
    return v

def extract_seq(buf, N, nbyte, tag):
    results = {}
    for i in xrange(N):
        x = unpack(buf[i*nbyte:i*nbyte+nbyte])
        results[tag % (i+1)] = x
    return results

class GenWithDelay(object):
    """Generator object with a one object deep history. A function is applied
    to the previous and current values, returning the results.
    
    Example of usage:
    >>> # Define a generator function:
    >>> def genfunc(N):
    ...     for i in range(N):
    ...        yield i
    >>>
    >>> # Define a simple function that just returns the previous and current values:
    >>> def pair(previous, now):
    ...     return (previous, now)
    >>>
    >>> # Now wrap the generator and function:
    >>> g = GenWithDelay(genfunc(5), pair)
    >>> for i in g:
    ...    print i
    (0, 1)
    (1, 2)
    (2, 3)
    (3, 4)
    """
    
    def __init__(self, generator, func):
        """Initialize the generator object.
        
        generator: A generator object to be wrapped
        """
        self.generator = generator
        self.func = func
        self.starting_up = True
        
    def __iter__(self):
        return self
    
    def next(self):  #@ReservedAssignment
        """Advance to the next object"""
        if self.starting_up:
            self.previous = self.generator.next()
            self.starting_up = False
            # Call myself recursively:
            return self.next()
        else:
            now = self.generator.next()
            retval, self.previous = self.func(self.previous, now), now
            return retval
        
#===============================================================================
#                           Packet Drivers
#===============================================================================

class GEMBase(object):
    """Base type for various GEM packet types."""
    
    SEC_COUNTER_MAX   = 16777216      # 256^3
    BYTE4_COUNTER_MAX = 4294967296    # 256^4
    BYTE5_COUNTER_MAX = 1099511627776 # 256^5

    def setup(self, source):
        """Set up the source to generate the requested packet type.
        
        source: An object with member function 
          write_with_response(prompt, expected_response)"""

        source.write_with_response("^^^SYSPKT%02d" % self.packet_format, "PKT\r\n")
        
    def get_packet_from(self, source):
        # Get the byte buffer:
        byte_buf = self.get_buffer_from(source)
        # Extract the packet from the buffer
        packet = self.extract_packet_from(byte_buf)
        return packet
        
    def get_buffer_from(self, source):
        """Get a bytearray from the designated source.
        
        source: An object with member function
          read(N)
        which should return the requested number of bytes as a string.
        
        Returns: A bytearray of the correct length."""

        # Get a string...
        str_buf = source.read(self.packet_length)

        # ... convert it to a bytearray...
        byte_buf = bytearray(str_buf)

        # ... then check it for integrity:
        self._check_ends(byte_buf)
        self._check_checksum(byte_buf)
        self._check_ID(byte_buf)
        
        return byte_buf
    
    def extract_packet_from(self, byte_buf):
        """Should be provided by a specializing class."""
        raise NotImplementedError
    
    def _check_ends(self, buf):
        if not (buf[0] == buf[-2] == 0xFE) or not(buf[1] == buf[-3] == 0xFF):
            raise weepwr.EndError('Bad header or footer')
        
    def _check_checksum(self, buf):
        chksum=sum(buf[:-1])
        if chksum & 0xff != buf[-1]:
            raise weepwr.CheckSumError("Bad checksum")
        
    def _check_ID(self, buf):
        if self.packet_ID != buf[2]:
            raise weepwr.EndError("Bad packet ID. Got %d, expected %d" % (buf[2], self.packet_ID))
    
    # Adapted from btmon.py:
    def _calc_secs(self, oldpkt, newpkt):
        ds = newpkt['secs'] - oldpkt['secs']
        if newpkt['secs'] < oldpkt['secs']:
            ds += GEMBase.SEC_COUNTER_MAX
        return ds

    # Adapted from btmon.py:
    # Absolute watt counter increases no matter which way the current goes
    # Polarized watt counter only increase if the current is positive
    # Every polarized count registers as an absolute count
    def _calc_pe(self, tag, ds, ret, prev):
        '''calculate power and energy for a 5-byte polarized counter'''

        # Detect counter wraparound and deal with it
        daws = ret[tag+'_aws'] - prev[tag+'_aws']
        if ret[tag+'_aws'] < prev[tag+'_aws']:
            daws += GEMBase.BYTE5_COUNTER_MAX
            syslog.syslog(syslog.LOG_WARNING, 'GEM: Energy counter wraparound detected for %s' % tag)
        dpws = ret[tag+'_pws'] - prev[tag+'_pws']
        if ret[tag+'_pws'] < prev[tag+'_pws']:
            dpws += GEMBase.BYTE5_COUNTER_MAX
            syslog.syslog(syslog.LOG_WARNING, 'GEM: Polarized energy counter wraparound detected for %s' % tag)

        # Calculate average power over the time period
        ret[tag+'_w'] = daws / ds
        pw = dpws / ds
        nw = ret[tag+'_w'] - pw

        ret[tag+'_pw'] = nw
        ret[tag+'_nw'] = pw

        # The polarized count goes up only if the sign is positive, so use the
        # value of polarized count to determine the sign of overall watts
        if (ret[tag+'_pw'] == 0):
            ret[tag+'_w'] *= -1

        # calculate the watt-hour count and delta
        pwh = ret[tag+'_pws'] / 3600.0
        nwh = (ret[tag+'_aws'] - ret[tag+'_pws']) / 3600.0

        ret[tag+'_pwh'] = nwh
        ret[tag+'_nwh'] = pwh
        prev_dwh = (prev[tag+'_aws'] - 2*prev[tag+'_pws']) / 3600.0
        ret[tag+'_wh'] = ret[tag+'_pwh'] - ret[tag+'_nwh']
        ret[tag+'_dwh'] = ret[tag+'_wh'] - prev_dwh
    
    # Adapted from btmon.py:
    def calculate(self, prev, now):
        '''calculate watts and watt-hours from watt-second counters'''

        # FIXME: check the reset flag once that is supported in gem packets
        # until then, if counter drops we assume it is due to a reset
        for x in range(1,self.NUM_CHAN+1):
            tag = 'ch%d' % x
            c0 = prev[tag+'_aws']
            c1 = now[tag+'_aws']
            if c1 < c0:
                raise weepwr.CounterResetError("channel: %s old: %d new: %d" % (tag, c0, c1))

        ret = now
        ds = self._calc_secs(prev, ret)
        for x in range(1,self.NUM_CHAN+1):
            tag = 'ch%d' % x
            self._calc_pe(tag, ds, ret, prev)

        # Add the interval time:
        ret['interval'] = ds / 60.0
        return ret

class GEM48PBinary(GEMBase):
    
    def __init__(self):
        super(GEM48PBinary, self).__init__()
        self.packet_length = 619
        self.packet_ID = 5
        self.packet_format = 5
        self.NUM_CHAN = 32 # there are 48 channels, but only 32 usable

    
    def extract_packet_from(self, byte_buf):
        
        packet = {'packet_id': self.packet_ID,
                  'dateTime' : int(time.time() + 0.5)}

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
        pulse = extract_seq(byte_buf[588:], 4, 3, 'p%d')
        packet.update(pulse)
        
        # Extract temperatures:
        temperature = extract_seq(byte_buf[600:], 8, 2, 't%d')
        packet.update(temperature)
        
        return packet

class GEM48PDBinary(GEM48PBinary):

    def __init__(self):
        super(GEM48PDBinary, self).__init__()
        self.packet_length = 625
        self.packet_format = 4
    
    def extract_packet_from(self, byte_buf):

        # Get the packet from my superclass
        packet = GEM48PBinary.extract_packet_from(self, byte_buf)
        
        # Add the embedded time:
        Y, M, D, h, m ,s = byte_buf[616:622]
        time_tt = (Y+2000, M, D, h, m, s, 0, 0, -1)
        packet['time_created'] = int(calendar.timegm(time_tt))
        
        return packet

#===============================================================================
#                                The driver
#===============================================================================

class GEM(object):
    
    def __init__(self, source, **gem_dict):
        """Initialize using a byte source.
        
        If the GEM has not been initialized to start sending packets by some
        other means, this can be done by calling method setup().

        Parameters:
        source: The source of the bytes. Should have methods read() and write().
        
        NAMED ARGUMENTS:
        
        PacketClass: The class for the expected packet type. Default is GEM48PDBinary.
        
        max_tries: The maximum number of tries that should be attempted in the event
        of a read error. Default is 3."""
        
        self.source = source
        self.max_tries = int(gem_dict.get('max_tries', 3))
        # Get the class of the packet type to use:
        PacketClass = gem_dict.get('packet_type', GEM48PDBinary)
        # Now instantiate an instance:
        self.packet_driver = PacketClass()

    def setup(self, send_interval=5):
        """Initialize the GEM to start sending packets.
        
        Parameters:
        send_interval: The time in seconds between packets. Default is 5."""
        
        self.source.flush_input()
        self.source.write_with_response("^^^SYSOFF", "OFF\r\n")
        self.source.write_with_response("^^^SYSIVL%03d" % send_interval, "IVL\r\n")
        self.packet_driver.setup(self.source)
        self.source.write_with_response("^^^SYS_ON", "_ON\r\n")
        
    def gen_packets(self):
        """Generator function that returns packets."""

        i_try = 1
        while True:
            try:
                packet = self.packet_driver.get_packet_from(self.source)
                yield packet
                i_try = 1
            except weepwr.WeePwrIOError, e:
                syslog.syslog(syslog.LOG_ERR, "GEM: Try #%d; I/O Error:%s" % (i_try, e))
                # Some sort of I/O Error. Reraise the error if we have exceeded
                # the max number of tries:
                if i_try >= self.max_tries:
                    raise
                i_try += 1
                
    def genLoopPackets(self):
        
        gen_func = GenWithDelay(self.gen_packets(), self.packet_driver.calculate)
        
        for packet in gen_func:
            yield packet
            
if __name__ == '__main__':

    import weeutil.weeutil
    
    source = SocketConnection.open(host='GEMCAT', tcp_port=8083)

    gem_ = GEM(source)
    gem_.setup()

    for ipacket, packet in enumerate(gem_.genLoopPackets()):
        print ipacket, weeutil.weeutil.timestamp_to_string(packet['time_created']), packet['volts'],

        for key in packet:
            if key.startswith('ch8'):
                print " %s = %s," % (key, packet[key]),
        
        print