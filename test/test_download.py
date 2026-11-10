"""Tests for download retry behavior."""

__author__ = "Tom Goetz"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"

import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import fitfile
from idbutils import DbParams

from garmindb.download import Download
from garmindb.garmin_connect_auth_adapter import GarminConnectRateLimitError
from garmindb.garmindb import Monitoring, MonitoringDb


class FakeConfig:
    """Minimal config stub for Download tests."""

    def get_session_file(self):
        return "/tmp/garth_session"

    def get_garmin_base_domain(self):
        return "garmin.com"


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.download = Download(FakeConfig())

    def test_get_stat_retries_until_success(self):
        attempts = []

        def flaky_stat(directory, day, overwrite):
            attempts.append((directory, day, overwrite))
            if len(attempts) < 3:
                raise RuntimeError("temporary HRV gap")

        start_date = datetime.date(2026, 3, 1)
        with mock.patch("garmindb.download.time.sleep"):
            self.download._Download__get_stat(flaky_stat, "/tmp/hrv", start_date, 1, False)

        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0][0], "/tmp/hrv")
        self.assertEqual(attempts[0][1], start_date)

    def test_get_stat_stops_after_five_failures(self):
        attempts = []

        def always_fail(directory, day, overwrite):
            attempts.append((directory, day, overwrite))
            raise RuntimeError("persistent HRV gap")

        start_date = datetime.date(2026, 3, 1)
        with mock.patch("garmindb.download.time.sleep"):
            self.download._Download__get_stat(always_fail, "/tmp/hrv", start_date, 1, False)

        self.assertEqual(len(attempts), 5)

    def test_get_stat_stops_on_rate_limit_without_retry(self):
        attempts = []

        def rate_limited(directory, day, overwrite):
            attempts.append(day)
            raise GarminConnectRateLimitError("too many requests (HTTP 429)")

        start_date = datetime.date(2026, 3, 1)
        with mock.patch("garmindb.download.time.sleep") as sleep:
            with self.assertRaises(GarminConnectRateLimitError):
                self.download._Download__get_stat(rate_limited, "/tmp/hrv", start_date, 10, False)

        self.assertEqual(attempts, [start_date])
        sleep.assert_not_called()

    def get_monitoring_days(self, start_date, days, skip_days=()):
        with mock.patch.object(self.download, "_Download__get_monitoring_day") as get_day, \
                mock.patch.object(self.download, "_Download__unzip_files"), \
                mock.patch("garmindb.download.time.sleep") as sleep, \
                mock.patch("garmindb.download.tempfile.mkdtemp", return_value="/tmp/monitoring"):
            self.download.get_monitoring(lambda year: f"/tmp/{year}", start_date, days, skip_days)
        requested = [call.args[0] for call in get_day.call_args_list]
        self.assertEqual(sleep.call_count, len(requested))
        return requested

    def test_get_monitoring_requests_every_day_by_default(self):
        start_date = datetime.date(2026, 3, 1)

        self.assertEqual(self.get_monitoring_days(start_date, 3), [start_date + datetime.timedelta(day) for day in range(3)])

    def test_get_monitoring_skips_days_without_a_request_or_pause(self):
        start_date = datetime.date(2026, 3, 1)
        skip_days = {start_date, start_date + datetime.timedelta(2)}

        self.assertEqual(self.get_monitoring_days(start_date, 3, skip_days), [start_date + datetime.timedelta(1)])

    def test_get_monitoring_always_requests_the_overlap_days(self):
        today = datetime.datetime.now().date()
        overlap = self.download.download_days_overlap
        start_date = today - datetime.timedelta(overlap + 1)
        days = overlap + 2
        skip_days = {start_date + datetime.timedelta(day) for day in range(days)}

        self.assertEqual(self.get_monitoring_days(start_date, days, skip_days), [start_date + datetime.timedelta(day) for day in range(1, days)])


class TestMonitoringCompleteDays(unittest.TestCase):
    def setUp(self):
        db_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, db_dir, True)
        self.db = MonitoringDb(DbParams(db_type='sqlite', db_path=db_dir))

    def add_entry(self, timestamp):
        entry = {'timestamp': timestamp, 'activity_type': fitfile.fields.ActivityType.walking, 'steps': 10}
        with self.db.managed_session() as session:
            Monitoring.s_insert_or_update(session, entry)

    def test_only_days_with_an_entry_at_the_end_of_the_day_are_complete(self):
        self.add_entry(datetime.datetime(2026, 3, 1, 0, 1))
        self.add_entry(datetime.datetime(2026, 3, 1, 23, 59, 59))
        self.add_entry(datetime.datetime(2026, 3, 2, 12, 0))
        # A day that started late is complete as well, the watch was just off in the morning.
        self.add_entry(datetime.datetime(2026, 3, 4, 8, 41))
        self.add_entry(datetime.datetime(2026, 3, 4, 23, 59, 59))

        complete_days = Monitoring.get_complete_days(self.db, datetime.date(2026, 3, 1), 4)

        self.assertEqual(complete_days, {datetime.date(2026, 3, 1), datetime.date(2026, 3, 4)})

    def test_days_outside_of_the_range_are_ignored(self):
        self.add_entry(datetime.datetime(2026, 2, 28, 23, 59, 59))
        self.add_entry(datetime.datetime(2026, 3, 2, 23, 59, 59))

        self.assertEqual(Monitoring.get_complete_days(self.db, datetime.date(2026, 3, 1), 1), set())

    def test_empty_table_has_no_complete_days(self):
        self.assertEqual(Monitoring.get_complete_days(self.db, datetime.date(2026, 3, 1), 3), set())


class TestSkipCompleteActivities(unittest.TestCase):
    """Tests for get_activities(skip_complete=True)."""

    strength = {'activityId': 1, 'activityType': {'typeKey': 'strength_training'}}
    cycling = {'activityId': 2, 'activityType': {'typeKey': 'cycling'}}

    def setUp(self):
        self.download = Download(FakeConfig())
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.directory = temp_dir.name

    def _touch(self, *filenames):
        for filename in filenames:
            open(os.path.join(self.directory, filename), 'w').close()

    def _requested_ids(self, activities, **kwargs):
        with mock.patch.object(self.download, '_Download__get_activity_summaries', return_value=activities), \
                mock.patch.object(self.download, 'get_activity') as get_activity, \
                mock.patch.object(self.download, '_Download__unzip_files'):
            self.download.get_activities(self.directory, 10, **kwargs)
        return [call.args[1]['activityId'] for call in get_activity.call_args_list]

    def _touch_complete(self):
        self._touch('activity_1.json', 'activity_details_1.json', 'activity_exercise_sets_1.json', '1_ACTIVITY.fit',
                    'activity_2.json', 'activity_details_2.json', '2_ACTIVITY.fit')

    def test_complete_activities_are_skipped(self):
        self._touch_complete()
        self.assertEqual(self._requested_ids([self.strength, self.cycling], skip_complete=True), [])

    def test_activity_with_a_missing_file_is_requested(self):
        for missing in ('activity_1.json', 'activity_details_1.json', 'activity_exercise_sets_1.json', '1_ACTIVITY.fit'):
            with self.subTest(missing=missing):
                self._touch_complete()
                os.remove(os.path.join(self.directory, missing))
                self.assertEqual(self._requested_ids([self.strength, self.cycling], skip_complete=True), [1])

    def test_default_requests_every_activity(self):
        self._touch_complete()
        self.assertEqual(self._requested_ids([self.strength, self.cycling]), [1, 2])

    def test_overwrite_wins(self):
        self._touch_complete()
        self.assertEqual(self._requested_ids([self.strength, self.cycling], overwrite=True, skip_complete=True), [1, 2])

    def test_no_activity_list(self):
        self.assertEqual(self._requested_ids(None, skip_complete=True), [])


class TestExerciseSetsRefresh(unittest.TestCase):
    """Tests for re-downloading the exercise sets of unchanged strength training activities."""

    strength = {'activityId': 1, 'activityType': {'typeKey': 'strength_training'}}
    cycling = {'activityId': 2, 'activityType': {'typeKey': 'cycling'}}
    sets = {'exerciseSets': [{'setType': 'ACTIVE', 'repetitionCount': 15}]}
    corrected_sets = {'exerciseSets': [{'setType': 'ACTIVE', 'repetitionCount': 16}]}

    def setUp(self):
        self.download = Download(FakeConfig())
        self.download.garmin = mock.Mock()
        self.download.garmin.connectapi.return_value = self.corrected_sets
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.directory = temp_dir.name
        self.sets_file = os.path.join(self.directory, 'activity_exercise_sets_1.json')
        # unchanged details, FIT file present: only the exercise sets can cause a request
        for patcher in (mock.patch.object(self.download, '_Download__save_activity_details', return_value=False),
                        mock.patch.object(self.download, '_activity_fit_file_exists', return_value=True),
                        mock.patch.object(self.download, '_Download__save_activity_file'),
                        mock.patch('garmindb.download.time.sleep')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_sets(self, json_data):
        with open(self.sets_file, 'w') as file:
            json.dump(json_data, file)
        os.utime(self.sets_file, (0, 0))

    def _read_sets(self):
        with open(self.sets_file) as file:
            return json.load(file)

    def _sets_requests(self):
        return [call for call in self.download.garmin.connectapi.call_args_list if call.args[0].endswith('/exerciseSets')]

    def test_existing_sets_are_not_requested_without_refresh(self):
        self._write_sets(self.sets)
        self.download.get_activity(self.directory, self.strength, False)
        self.assertEqual(self._sets_requests(), [])
        self.assertEqual(self._read_sets(), self.sets)

    def test_refresh_updates_changed_sets(self):
        self._write_sets(self.sets)
        self.download.get_activity(self.directory, self.strength, False, refresh_exercise_sets=True)
        self.assertEqual(len(self._sets_requests()), 1)
        self.assertEqual(self._read_sets(), self.corrected_sets)

    def test_refresh_keeps_unchanged_file(self):
        self._write_sets(self.corrected_sets)
        self.download.get_activity(self.directory, self.strength, False, refresh_exercise_sets=True)
        self.assertEqual(len(self._sets_requests()), 1)
        self.assertEqual(os.path.getmtime(self.sets_file), 0)

    def test_missing_sets_are_downloaded_without_refresh(self):
        self.download.get_activity(self.directory, self.strength, False)
        self.assertEqual(len(self._sets_requests()), 1)
        self.assertEqual(self._read_sets(), self.corrected_sets)

    def test_overwrite_rewrites_unchanged_file(self):
        self._write_sets(self.corrected_sets)
        self.download.get_activity(self.directory, self.strength, True)
        self.assertEqual(len(self._sets_requests()), 1)
        self.assertNotEqual(os.path.getmtime(self.sets_file), 0)

    def test_other_sports_are_not_requested(self):
        self.download.get_activity(self.directory, self.cycling, False, refresh_exercise_sets=True)
        self.assertEqual(self._sets_requests(), [])

    def test_get_activities_passes_refresh_on(self):
        with mock.patch.object(self.download, '_Download__get_activity_summaries', return_value=[self.strength]), \
                mock.patch.object(self.download, 'get_activity') as get_activity, \
                mock.patch.object(self.download, '_Download__unzip_files'):
            self.download.get_activities(self.directory, 10, refresh_exercise_sets=True)
        get_activity.assert_called_once_with(self.directory, self.strength, False, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
