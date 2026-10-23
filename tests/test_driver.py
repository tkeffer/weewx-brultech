#
#    Copyright (c) 2013-2026 Tom Keffer <tkeffer@gmail.com> and Manuel Hilbert
#
#    See the file LICENSE.txt for your full rights.
#
"""The driver against a fake GEM on a local socket. No hardware involved."""
import pytest

import weewx

from conftest import build_packet
from user.brultech import Brultech


def make_driver(gem, packet_type='GEMBin48NetTime'):
    return Brultech(packet_type=packet_type, poll_interval=0.01, max_channels=32,
                    socket={'host': '127.0.0.1', 'port': gem.port, 'send_delay': 0.01,
                            'timeout': 5})


def test_packet_values(fake_gem):
    packet_in = build_packet(abs_ws={1: 2 ** 33 + 5, 32: 99}, amps={1: 12.5, 2: 0.04},
                             pulses={1: 70000}, temps={1: 21.5, 2: -3.0})
    gem = fake_gem([packet_in])
    driver = make_driver(gem)
    try:
        packet = next(driver.genLoopPackets())
    finally:
        driver.closePort()

    # The setup handshake the driver is expected to perform:
    assert gem.commands[:4] == [b'^^^SYSOFF', b'^^^SYSKAI0', b'^^^TMPDGC', b'^^^SYSPKT04']
    assert packet['usUnits'] == weewx.METRICWX
    assert packet['serial'] == '00701234'
    assert packet['ch1_volt'] == 120.0
    assert packet['ch1_a_energy2'] == 2 ** 33 + 5
    assert packet['ch32_a_energy2'] == 99
    assert 'ch33_a_energy2' not in packet
    assert packet['ch1_amp'] == 12.5
    assert packet['ch2_amp'] == 0.04
    assert packet['ch1_count'] == 70000
    assert packet['ch1_temperature'] == 21.5
    assert packet['ch2_temperature'] == -3.0
    assert 'ch3_temperature' not in packet
    assert packet['secs'] == 3600
    assert packet['time_created'] == 1662553815


def test_packet_without_time(fake_gem):
    gem = fake_gem([build_packet(length=619, abs_ws={1: 5})])
    driver = make_driver(gem, 'GEMBin48Net')
    try:
        packet = next(driver.genLoopPackets())
    finally:
        driver.closePort()
    assert gem.commands[3] == b'^^^SYSPKT05'
    assert packet['ch1_a_energy2'] == 5
    assert 'time_created' not in packet


def test_fragmented_packet(fake_gem):
    """TCP may deliver a packet in several pieces."""
    gem = fake_gem([build_packet(abs_ws={1: 42})], chunks=[200, 200])
    driver = make_driver(gem)
    try:
        packet = next(driver.genLoopPackets())
    finally:
        driver.closePort()
    assert packet['ch1_a_energy2'] == 42


def test_bad_checksum(fake_gem):
    bad = build_packet()
    bad[-1] ^= 0xff
    gem = fake_gem([bad])
    driver = make_driver(gem)
    try:
        with pytest.raises(weewx.WeeWxIOError, match='checksum'):
            next(driver.genLoopPackets())
    finally:
        driver.closePort()


def test_set_time(fake_gem):
    gem = fake_gem([build_packet()])
    driver = make_driver(gem)
    try:
        # 12:30:15.5 UTC. The driver adds 0.75 s to allow for the transmission delay.
        driver.setTime(1662553815.5)
    finally:
        driver.closePort()
    assert gem.commands[-1] == b'^^^SYSDTM22,09,07,12,30,16\r'
