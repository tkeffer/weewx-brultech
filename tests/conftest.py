#
#    Copyright (c) 2013-2026 Tom Keffer <tkeffer@gmail.com> and Manuel Hilbert
#
#    See the file LICENSE.txt for your full rights.
#
"""Shared fixtures for the weewx-brultech tests.

The fixtures stand in for the energy monitor: ``build_packet()`` encodes a
binary GEM packet, and ``FakeGem`` serves such packets over a local TCP
socket the way a GEM in server mode does.
"""
import os
import socket
import struct
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bin'))

# The device answers each command with one of these strings.
REPLIES = {
    b'^^^SYSOFF': b'OFF\r\n',
    b'^^^SYSKAI0': b'OK\r\n',
    b'^^^TMPDGC': b'C',
    b'^^^SYSPKT04': b'PKT\r\n',
    b'^^^SYSPKT05': b'PKT\r\n',
}


def build_packet(length=625, volts=120.0, ser_no=1234, unit_id=7, secs=3600,
                 abs_ws=None, amps=None, pulses=None, temps=None,
                 when=(22, 9, 7, 12, 30, 15)):
    """Encode a binary GEM packet (format 4 with 625 bytes, or format 5 with 619).

    Args:
        length (int): 625 for BIN48-NET-TIME, 619 for BIN48-NET.
        volts (float): Line voltage.
        ser_no (int): Serial number.
        unit_id (int): Unit ID.
        secs (int): Device uptime in seconds.
        abs_ws (dict): Absolute watt-seconds, keyed by channel number (1-based).
        amps (dict): Current in amps, keyed by channel number.
        pulses (dict): Pulse counts, keyed by channel number (1 to 4).
        temps (dict): Temperatures in degrees C, keyed by channel number (1 to 8).
        when (tuple): The device clock as (year mod 2000, month, day, hour, minute, second).

    Returns:
        bytearray: The packet, with a valid header, footer and checksum.
    """
    buf = bytearray(length)
    buf[0] = 0xFE
    buf[1] = 0xFF
    buf[2] = 5
    buf[3:5] = struct.pack('>H', int(volts * 10))
    for ch, ws in (abs_ws or {}).items():
        buf[5 + (ch - 1) * 5:10 + (ch - 1) * 5] = ws.to_bytes(5, 'little')
    buf[485:487] = struct.pack('>H', ser_no)
    buf[488] = unit_id
    for ch, a in (amps or {}).items():
        buf[489 + (ch - 1) * 2:491 + (ch - 1) * 2] = int(a * 50).to_bytes(2, 'little')
    buf[585:588] = secs.to_bytes(3, 'little')
    for ch, n in (pulses or {}).items():
        buf[588 + (ch - 1) * 3:591 + (ch - 1) * 3] = n.to_bytes(3, 'little')
    # Sensors that are not connected read 0x7FFF, which the driver treats as out of range.
    for ch in range(1, 9):
        raw = bytearray(b'\xff\x7f')
        if ch in (temps or {}):
            t = temps[ch]
            half = int(abs(t) * 2)
            raw = bytearray([half & 0xff, (half >> 8) | (0x80 if t < 0 else 0)])
        buf[600 + (ch - 1) * 2:602 + (ch - 1) * 2] = raw
    if length == 625:
        buf[616:622] = bytes(when)
    buf[-3] = 0xFF
    buf[-2] = 0xFE
    buf[-1] = sum(buf[:-1]) & 0xff
    return buf


class FakeGem(object):
    """A GEM in server mode on a local TCP port. Serves a fixed list of packets, one per poll.

    Args:
        packets (list): Packets to send, in order. The last one repeats.
        chunks (list): Sizes into which each packet is split on the wire. A pause follows each
            chunk, so the driver sees partial reads.
    """

    def __init__(self, packets, chunks=None):
        self.packets = packets
        self.chunks = chunks
        self.commands = []
        self.n_polls = 0
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.server.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with conn:
            while True:
                cmd = conn.recv(64)
                if not cmd:
                    return
                self.commands.append(bytes(cmd))
                if cmd in REPLIES:
                    conn.sendall(REPLIES[cmd])
                elif cmd.startswith(b'^^^SYSDTM'):
                    conn.sendall(b'DTM\r\n')
                elif cmd == b'^^^APISPK':
                    packet = self.packets[min(self.n_polls, len(self.packets) - 1)]
                    self.n_polls += 1
                    self.send_in_chunks(conn, bytes(packet))

    def send_in_chunks(self, conn, data):
        if not self.chunks:
            conn.sendall(data)
            return
        pos = 0
        for size in self.chunks:
            conn.sendall(data[pos:pos + size])
            pos += size
            time.sleep(0.05)
        conn.sendall(data[pos:])

    def close(self):
        self.server.close()


@pytest.fixture
def fake_gem():
    """Start a FakeGem. Call the fixture with the packets to serve."""
    started = []

    def start(packets, chunks=None):
        gem = FakeGem(packets, chunks)
        started.append(gem)
        return gem

    yield start
    for gem in started:
        gem.close()
