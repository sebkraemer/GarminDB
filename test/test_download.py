"""Tests for download retry behavior."""

__author__ = "Tom Goetz"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"

import datetime
import os
import tempfile
import unittest
from unittest import mock

from garmindb.download import Download


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
