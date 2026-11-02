"""Test Download integration with the Garmin Connect auth adapter."""

__author__ = "Ilya Verchenko"
__copyright__ = "Copyright Ilya Verchenko"
__license__ = "GPL"

import json
import datetime
import io
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from garmindb.download import Download


class FakeConfig:
    def __init__(self, fit_files_dir):
        self.fit_files_dir = fit_files_dir

    def get_fit_files_dir(self):
        return self.fit_files_dir


class FakeAdapter:
    instances = []

    def __init__(self, _gc_config):
        self.profile = {"displayName": "display", "fullName": "Full Name"}
        self.display_name = "display"
        self.full_name = "Full Name"
        self.connectapi_calls = []
        self.download_calls = []
        FakeAdapter.instances.append(self)

    def login(self):
        return True

    def connectapi(self, path, **kwargs):
        self.connectapi_calls.append((path, kwargs))
        return {"path": path, "kwargs": kwargs}

    def download(self, path):
        self.download_calls.append(path)
        return b"binary-data"


class TestDownloadAuthAdapter(unittest.TestCase):
    def setUp(self):
        FakeAdapter.instances = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = FakeConfig(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_download(self):
        with patch("garmindb.download.GarminConnectAuthAdapter", FakeAdapter):
            return Download(self.config)

    def test_login_uses_adapter_and_saves_profile_files(self):
        download = self.create_download()

        self.assertTrue(download.login())

        adapter = FakeAdapter.instances[0]
        self.assertEqual(download.display_name, "display")
        self.assertEqual(download.full_name, "Full Name")
        self.assertEqual(
            adapter.connectapi_calls,
            [
                ("/userprofile-service/userprofile/user-settings", {}),
                ("/userprofile-service/userprofile/personal-information", {}),
            ],
        )

        with open(os.path.join(self.temp_dir.name, "social-profile.json"), encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"displayName": "display", "fullName": "Full Name"})

    def test_save_binary_file_uses_adapter_download(self):
        download = self.create_download()
        filename = os.path.join(self.temp_dir.name, "activity.zip")

        download.save_binary_file(filename, "/download-service/files/activity/123")

        self.assertEqual(FakeAdapter.instances[0].download_calls, ["/download-service/files/activity/123"])
        with open(filename, "rb") as file:
            self.assertEqual(file.read(), b"binary-data")

    def test_summary_download_uses_adapter_connectapi(self):
        download = self.create_download()
        download.display_name = "display"

        download._Download__get_summary_day(
            lambda _year: self.temp_dir.name,
            datetime.date(2024, 1, 2),
            overwrite=True,
        )

        path, kwargs = FakeAdapter.instances[0].connectapi_calls[0]
        self.assertEqual(path, "/usersummary-service/usersummary/daily/display")
        self.assertEqual(kwargs["params"]["calendarDate"], "2024-01-02")

    def test_activity_files_downloaded_before_an_error_are_unzipped(self):
        download = self.create_download()
        adapter = FakeAdapter.instances[0]
        activities = [{"activityId": 1, "activityName": "one"}, {"activityId": 2, "activityName": "two"}]

        def connectapi(path, **kwargs):
            adapter.connectapi_calls.append((path, kwargs))
            if path.startswith("/activitylist-service"):
                return activities
            if path.endswith("/2"):
                raise RuntimeError("stop in the middle")
            return {"metadataDTO": {"lastUpdateDate": "2026-10-01T00:00:00.0"}}

        def zipped_fit(path):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(f"{path.rsplit('/', 1)[-1]}_ACTIVITY.fit", b"fit")
            return buffer.getvalue()

        adapter.connectapi = connectapi
        adapter.download = zipped_fit
        with patch("garmindb.download.time.sleep"):
            with self.assertRaises(RuntimeError):
                download.get_activities(self.temp_dir.name, 2)
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir.name, "1_ACTIVITY.fit")))

    def prepare_activity(self, download, last_update="2026-10-01T00:00:00.0"):
        """Make the fake adapter return activity details with the given lastUpdateDate."""
        adapter = FakeAdapter.instances[0]

        def connectapi(path, **kwargs):
            adapter.connectapi_calls.append((path, kwargs))
            return {"metadataDTO": {"lastUpdateDate": last_update}}

        adapter.connectapi = connectapi
        download.temp_dir = self.temp_dir.name
        return adapter

    def write_file(self, name, content="{}"):
        with open(os.path.join(self.temp_dir.name, name), "w", encoding="utf-8") as file:
            file.write(content)

    def test_missing_activity_files_are_downloaded_when_details_are_unchanged(self):
        download = self.create_download()
        adapter = self.prepare_activity(download)
        self.write_file("activity_details_42.json", '{"metadataDTO": {"lastUpdateDate": "2026-10-01T00:00:00.0"}}')

        with patch("garmindb.download.time.sleep"):
            download.get_activity(self.temp_dir.name, {"activityId": 42, "activityName": "walk"}, False)

        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir.name, "activity_42.json")))
        self.assertEqual(adapter.download_calls, ["/download-service/files/activity/42"])

    def test_complete_activity_is_not_downloaded_again(self):
        download = self.create_download()
        adapter = self.prepare_activity(download)
        self.write_file("activity_details_42.json", '{"metadataDTO": {"lastUpdateDate": "2026-10-01T00:00:00.0"}}')
        self.write_file("activity_42.json")
        self.write_file("42_ACTIVITY.fit", "fit")

        with patch("garmindb.download.time.sleep"):
            download.get_activity(self.temp_dir.name, {"activityId": 42, "activityName": "walk"}, False)

        self.assertEqual([path for path, _kwargs in adapter.connectapi_calls], ["/activity-service/activity/42"])
        self.assertEqual(adapter.download_calls, [])

    def test_overwrite_downloads_the_fit_file_again(self):
        download = self.create_download()
        adapter = self.prepare_activity(download)
        self.write_file("activity_details_42.json", '{"metadataDTO": {"lastUpdateDate": "2026-10-01T00:00:00.0"}}')
        self.write_file("activity_42.json")
        self.write_file("42_ACTIVITY.fit", "fit")

        with patch("garmindb.download.time.sleep"):
            download.get_activity(self.temp_dir.name, {"activityId": 42, "activityName": "walk"}, True)

        self.assertEqual(adapter.download_calls, ["/download-service/files/activity/42"])

    def strength_activity(self):
        return {"activityId": 42, "activityName": "strength", "activityType": {"typeKey": "strength_training"}}

    def requested_paths(self, adapter):
        return [path for path, _kwargs in adapter.connectapi_calls]

    def test_exercise_sets_downloaded_for_new_strength_training(self):
        download = self.create_download()
        adapter = self.prepare_activity(download)

        with patch("garmindb.download.time.sleep"):
            download.get_activity(self.temp_dir.name, self.strength_activity(), False)

        self.assertIn("/activity-service/activity/42/exerciseSets", self.requested_paths(adapter))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir.name, "activity_exercise_sets_42.json")))

    def test_exercise_sets_not_requested_for_other_sports(self):
        download = self.create_download()
        adapter = self.prepare_activity(download)

        with patch("garmindb.download.time.sleep"):
            download.get_activity(self.temp_dir.name, {"activityId": 42, "activityType": {"typeKey": "running"}}, False)

        self.assertNotIn("/activity-service/activity/42/exerciseSets", self.requested_paths(adapter))

    def test_existing_exercise_sets_not_requested_when_details_unchanged(self):
        download = self.create_download()
        adapter = self.prepare_activity(download)
        self.write_file("activity_details_42.json", '{"metadataDTO": {"lastUpdateDate": "2026-10-01T00:00:00.0"}}')
        self.write_file("activity_exercise_sets_42.json", '{"exerciseSets": []}')

        with patch("garmindb.download.time.sleep"):
            download.get_activity(self.temp_dir.name, self.strength_activity(), False)

        self.assertNotIn("/activity-service/activity/42/exerciseSets", self.requested_paths(adapter))

    def test_exercise_sets_downloaded_again_when_details_changed(self):
        download = self.create_download()
        adapter = self.prepare_activity(download, last_update="2026-10-05T00:00:00.0")
        self.write_file("activity_details_42.json", '{"metadataDTO": {"lastUpdateDate": "2026-10-01T00:00:00.0"}}')
        self.write_file("activity_exercise_sets_42.json", '{"exerciseSets": []}')

        with patch("garmindb.download.time.sleep"):
            download.get_activity(self.temp_dir.name, self.strength_activity(), False)

        self.assertIn("/activity-service/activity/42/exerciseSets", self.requested_paths(adapter))
        with open(os.path.join(self.temp_dir.name, "activity_exercise_sets_42.json"), encoding="utf-8") as file:
            self.assertIn("metadataDTO", json.load(file))


if __name__ == "__main__":
    unittest.main(verbosity=2)
