"""Tests for download retry behavior."""

__author__ = "Tom Goetz"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"

import datetime
import json
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
        with self.assertLogs(level='INFO') as logs:
            self.download.get_activity(self.directory, self.strength, False, refresh_exercise_sets=True)
        self.assertEqual(len(self._sets_requests()), 1)
        self.assertEqual(self._read_sets(), self.corrected_sets)
        self.assertTrue(any('Updating changed' in line and self.sets_file in line for line in logs.output))

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
