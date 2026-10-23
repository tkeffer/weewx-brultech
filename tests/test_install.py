#
#    Copyright (c) 2013-2026 Tom Keffer <tkeffer@gmail.com> and Manuel Hilbert
#
#    See the file LICENSE.txt for your full rights.
#
"""The installer and the configuration it writes."""
import io
import os
import sys

import configobj

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import install  # noqa: E402
from user.brultech import BrultechConfigurator, DEFAULTS_INI, energy2_re  # noqa: E402


def test_installer_files_exist():
    installer = install.loader()
    listed = set()
    for _, paths in installer['files']:
        for path in paths:
            assert os.path.isfile(os.path.join(ROOT, path)), path
            listed.add(path)
    # Everything in the payload directories is installed.
    for top in ('bin', 'skins'):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, top)):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for name in filenames:
                assert os.path.relpath(os.path.join(dirpath, name), ROOT) in listed


def test_installer_config():
    installer = install.loader()
    assert installer['config']['Brultech']['driver'] == 'user.brultech'
    assert installer['data_services'] == 'user.brultech.BrultechService'
    # Both places that hold the stanza agree on the driver module.
    stanza = configobj.ConfigObj(io.StringIO(DEFAULTS_INI))
    assert stanza['Brultech']['driver'] == 'user.brultech'
    assert installer['config']['Brultech']['stale'] == stanza['Brultech']['stale']


def test_show_info():
    """``weectl device --info`` decodes the monitor's configuration dump."""
    values = [0] * 125
    values[1] = 0x40  # Channel 1: standard metering, positive polarity
    values[2] = 0xC0  # Channel 2: standard metering, negative polarity
    values[49] = 211  # Channel 1 CT type: M-40
    values[50] = 146  # Channel 2 CT type: S-100 ...
    values[98] = 0x10  # ... with range 1 in the high nibble of byte 98 (channel 2): S-400
    values[123] = 4
    values[124] = 5
    reply = b'ALL\r\n' + b','.join(b'%02X' % v for v in values)

    class Device(object):
        max_channels = 2

        @staticmethod
        def get_info():
            return reply

    out = io.StringIO()
    BrultechConfigurator.show_info(Device(), dest=out)
    lines = out.getvalue().splitlines()
    assert 'M-40' in lines[0]
    assert 'S-400' in lines[1]
    assert lines[-1] == 'Packet send interval: 5s'


def test_skin_summaries_use_accumulated_energy():
    """The statistics and monthly summaries take ``.diff`` of each listed energy type.

    That is the total used only for accumulated energy. The difference of two delta energies
    means nothing.
    """
    skin = configobj.ConfigObj(os.path.join(ROOT, 'skins', 'SeasonsPower', 'skin.conf'))
    energies = [x for x in skin['DisplayOptions']['observations_stats'] if x.endswith('energy2')]
    assert energies
    assert all(energy2_re.match(x) for x in energies)
