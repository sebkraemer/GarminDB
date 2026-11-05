"""Test importing the self evaluation of activities from Garmin Connect details JSON files."""

__author__ = "Sebastian Krämer"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"

import json
import os
import shutil
import tempfile
import unittest

import fitfile
from idbutils import DbParams

from garmindb import GarminJsonDetailsData
from garmindb.garmindb import ActivitiesDb, ActivitiesEvaluations


ACTIVITY_ID = 10000000002


class TestSelfEvaluationJson(unittest.TestCase):
    """Test the text levels and raw values of the self evaluation."""

    def setUp(self):
        self.db_dir = tempfile.mkdtemp()
        self.input_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.db_dir, True)
        self.addCleanup(shutil.rmtree, self.input_dir, True)
        self.db_params = DbParams(db_type='sqlite', db_path=self.db_dir)
        self.activities_db = ActivitiesDb(self.db_params)

    def import_details(self, **summary):
        details = {
            'activityId': ACTIVITY_ID,
            'metadataDTO': {},
            'activityTypeDTO': {'typeId': 13, 'typeKey': 'strength_training', 'parentTypeId': 29},
            'summaryDTO': {'startTimeLocal': '2026-10-02T19:00:00.0', 'startTimeGMT': '2026-10-02T17:00:00.0', **summary},
        }
        with open(os.path.join(self.input_dir, f'activity_details_{ACTIVITY_ID}.json'), 'w') as file:
            json.dump(details, file)
        GarminJsonDetailsData(self.db_params, self.input_dir, False, fitfile.MeasurementSystem.metric, 0, simple_output=True).process()
        return ActivitiesEvaluations.get_activity(self.activities_db, str(ACTIVITY_ID))

    def test_raw_values_keep_the_precision_of_the_levels(self):
        for rpe in (50, 60):
            evaluation = self.import_details(directWorkoutRpe=rpe, directWorkoutFeel=75)[0]
            self.assertEqual(evaluation.self_eval_effort, 'Hard')
            self.assertEqual(evaluation.self_eval_effort_value, rpe)
            self.assertEqual(evaluation.self_eval_feel, 'Strong')
            self.assertEqual(evaluation.self_eval_feel_value, 75)

    def test_feel_of_zero_without_rpe_is_stored(self):
        evaluation = self.import_details(directWorkoutFeel=0)[0]
        self.assertEqual(evaluation.self_eval_feel, 'Very Weak')
        self.assertEqual(evaluation.self_eval_feel_value, 0)
        self.assertIsNone(evaluation.self_eval_effort_value)

    def test_activity_without_evaluation_gets_no_row(self):
        self.assertEqual(self.import_details(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
