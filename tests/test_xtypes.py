#
#    Copyright (c) 2013-2026 Tom Keffer <tkeffer@gmail.com> and Manuel Hilbert
#
#    See the file LICENSE.txt for your full rights.
#
"""Power and delta energy, and the aggregates computed from them, against a real SQLite database."""
import collections

import pytest

import weedb
import weewx
import weewx.manager
import weewx.xtypes
from weeutil.weeutil import TimeSpan

import user.gem_schema
from user.brultech import BTExtends, BrultechService, augment_record

T0 = 1662508800  # 2022-09-07 00:00:00 UTC, a multiple of a day
DAY = 86400


class FakeEngine(object):
    def __init__(self):
        self.callbacks = collections.defaultdict(list)

    def bind(self, event_type, callback):
        self.callbacks[event_type].append(callback)


@pytest.fixture
def service():
    svc = BrultechService(FakeEngine(), {})
    yield svc
    svc.shutDown()


@pytest.fixture
def manager(tmp_path, service):
    db_dict = {'database_name': str(tmp_path / 'bt.sdb'), 'driver': 'weedb.sqlite'}
    mgr = weewx.manager.DaySummaryManager.open_with_create(db_dict, schema=user.gem_schema.schema)
    yield mgr
    mgr.close()


def archive_records(start, n, step=300, watts=600, with_power=True, energy0=10 ** 10):
    """Records with a constant load, passed through the same code the service uses.

    Without ``with_power`` the power field is dropped, so the database never gets a power column
    and power has to be derived from the delta energy.
    """
    records = []
    prev = None
    for i in range(n):
        rec = {'dateTime': start + i * step, 'usUnits': weewx.METRICWX, 'interval': step // 60,
               'ch1_a_energy2': energy0 + i * step * watts}
        augment_record(rec, prev)
        prev = rec
        records.append(rec if with_power else {k: v for k, v in rec.items() if not k.endswith('_power')})
    return records


def test_power_and_delta(service):
    first, second = archive_records(T0, 2)
    assert 'ch1_ad_energy2' not in first
    assert second['ch1_ad_energy2'] == 300 * 600
    assert second['ch1_a_power'] == 600


def test_no_delta_across_a_gap(service):
    """A previous record older than ``stale`` is not used. The energy of the gap is not booked to one record."""
    _, after = archive_records(T0, 2)
    gapped = {'dateTime': T0 + 7200, 'usUnits': weewx.METRICWX, 'interval': 5,
              'ch1_a_energy2': after['ch1_a_energy2'] + 7200 * 600}
    augment_record(gapped, after, 1800)
    assert 'ch1_ad_energy2' not in gapped
    assert 'ch1_a_power' not in gapped
    follower = {'dateTime': T0 + 7500, 'usUnits': weewx.METRICWX, 'interval': 5,
                'ch1_a_energy2': gapped['ch1_a_energy2'] + 300 * 600}
    augment_record(follower, gapped, 1800)
    assert follower['ch1_ad_energy2'] == 300 * 600


@pytest.mark.parametrize('with_power', [True, False])
def test_average_power(manager, with_power):
    for rec in archive_records(T0, 12, with_power=with_power):
        manager.addRecord(rec)
    value = weewx.xtypes.get_aggregate('ch1_a_power', TimeSpan(T0, T0 + 12 * 300), 'avg', manager)
    assert value[0] == pytest.approx(600)
    assert value[1:] == ('watt', 'group_power')


@pytest.mark.parametrize('with_power', [True, False])
def test_power_series(manager, with_power):
    for rec in archive_records(T0, 12, with_power=with_power):
        manager.addRecord(rec)
    start, stop, data = weewx.xtypes.get_series('ch1_a_power', TimeSpan(T0, T0 + 12 * 300), manager)
    assert len(data[0]) == 11
    assert all(v == pytest.approx(600) for v in data[0])
    assert stop[0][0] - start[0][0] == 300


def test_diff_across_a_gap(manager):
    """Records exist on day 1 and day 3, none on day 2."""
    # The accumulated energy keeps counting through the gap, as the device does.
    after_gap = archive_records(T0 + 2 * DAY + 300, 12, energy0=10 ** 10 + 2 * DAY * 600)
    for rec in archive_records(T0, 12) + after_gap:
        manager.addRecord(rec)
    day1 = weewx.xtypes.get_aggregate('ch1_a_energy2', TimeSpan(T0, T0 + DAY), 'diff', manager)
    day2 = weewx.xtypes.get_aggregate('ch1_a_energy2', TimeSpan(T0 + DAY, T0 + 2 * DAY), 'diff',
                                      manager)
    assert day1[0] == 11 * 300 * 600
    assert day2[0] is None


def test_series_from_delta_energy(manager):
    """BTExtends computes a power series from delta energy, whether or not a power column exists."""
    for rec in archive_records(T0, 12, with_power=False):
        manager.addRecord(rec)
    start, stop, data = BTExtends.get_series('ch1_a_power', TimeSpan(T0, T0 + 12 * 300), manager)
    assert len(data[0]) == 11
    assert all(v == pytest.approx(600) for v in data[0])
