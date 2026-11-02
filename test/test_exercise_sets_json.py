"""Test importing the exercise sets of strength training activities from Garmin Connect JSON files."""

__author__ = "Sebastian Krämer"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"

import datetime
import json
import os
import tempfile
import unittest

import fitfile
from idbutils import DbParams

from garmindb import GarminJsonExerciseSetsData
from garmindb.garmindb import ActivitiesDb, ActivitySets


ACTIVITY_ID = 10000000001


def exercise_set(start_time, set_type='ACTIVE', category=None, name=None, reps=None, weight=None, duration=60.0, step=None, duplicate=False):
    """Return an exerciseSets entry shaped like the Garmin Connect response (anonymized)."""
    exercises = [{'category': category, 'name': name, 'probability': 100.0}] if category else []
    if duplicate:
        exercises.append({'category': category, 'name': 'SOMETHING_ELSE', 'probability': 0.0})
    return {
        'exercises': exercises,
        'duration': duration,
        'repetitionCount': reps,
        'weight': weight,
        'setType': set_type,
        'startTime': start_time,
        'wktStepIndex': step,
        'messageIndex': None,
    }


class TestExerciseSetsJson(unittest.TestCase):
    """Class for testing the import of Garmin Connect exerciseSets JSON files."""

    def setUp(self):
        self.db_params = DbParams(db_type='sqlite', db_path=tempfile.mkdtemp())
        self.input_dir = tempfile.mkdtemp()
        self.activities_db = ActivitiesDb(self.db_params)

    def write_details(self, start_local='2026-10-02T19:00:00.0', start_gmt='2026-10-02T17:00:00.0'):
        details = {'activityId': ACTIVITY_ID, 'summaryDTO': {'startTimeLocal': start_local, 'startTimeGMT': start_gmt}}
        with open(os.path.join(self.input_dir, f'activity_details_{ACTIVITY_ID}.json'), 'w') as file:
            json.dump(details, file)

    def import_sets(self, sets):
        with open(os.path.join(self.input_dir, f'activity_exercise_sets_{ACTIVITY_ID}.json'), 'w') as file:
            json.dump({'activityId': ACTIVITY_ID, 'exerciseSets': sets}, file)
        exercise_sets_data = GarminJsonExerciseSetsData(self.db_params, self.input_dir, False, fitfile.MeasurementSystem.metric, 0, simple_output=True)
        exercise_sets_data.process()
        return ActivitySets.get_activity(self.activities_db, ACTIVITY_ID)

    def test_sets_are_sorted_converted_and_stored(self):
        self.write_details()
        rows = self.import_sets([
            exercise_set('2026-10-02T17:03:00.0', category='CRUNCH', reps=30),
            exercise_set('2026-10-02T17:00:00.0', category='ELLIPTICAL', duration=300.0),
            exercise_set('2026-10-02T17:01:00.0', category='BENCH_PRESS', name='BARBELL_BENCH_PRESS', reps=10, weight=40000.0, step=1, duplicate=True),
            exercise_set('2026-10-02T17:02:00.0', set_type='REST', weight=-1.0, step=2),
        ])

        self.assertEqual([row.set_index for row in rows], [0, 1, 2, 3])
        self.assertEqual([row.exercise_category for row in rows], ['ELLIPTICAL', 'BENCH_PRESS', None, 'CRUNCH'])
        bench_press = rows[1]
        self.assertEqual(bench_press.set_type, 'ACTIVE')
        self.assertEqual(bench_press.start_time, datetime.datetime(2026, 10, 2, 19, 1))
        self.assertEqual(bench_press.exercise_name, 'BARBELL_BENCH_PRESS')
        self.assertEqual(bench_press.repetitions, 10)
        self.assertEqual(bench_press.weight, 40.0)
        self.assertEqual(bench_press.workout_step_index, 1)
        self.assertEqual(rows[0].duration, datetime.time(0, 5))
        self.assertIsNone(rows[2].weight)
        self.assertEqual(rows[2].set_type, 'REST')
        self.assertIsNone(rows[3].exercise_name)
        self.assertIsNone(rows[3].workout_step_index)

    def test_reimport_applies_corrections(self):
        self.write_details()
        self.import_sets([
            exercise_set('2026-10-02T17:00:00.0', category='CURL', name='DUMBBELL_BICEPS_CURL', reps=12, weight=10000.0),
            exercise_set('2026-10-02T17:01:00.0', category='CURL', name='DUMBBELL_BICEPS_CURL', reps=12, weight=10000.0),
            exercise_set('2026-10-02T17:02:00.0', category='CURL', name='DUMBBELL_BICEPS_CURL', reps=12, weight=10000.0),
        ])
        rows = self.import_sets([
            exercise_set('2026-10-02T17:00:00.0', category='CURL', name='HAMMER_CURL', reps=10, weight=12000.0),
            exercise_set('2026-10-02T17:01:00.0', category='CURL', name='HAMMER_CURL', reps=15),
        ])

        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0].exercise_name, rows[0].repetitions, rows[0].weight), ('HAMMER_CURL', 10, 12.0))
        self.assertIsNone(rows[1].weight)

    def test_missing_details_leave_start_time_empty(self):
        rows = self.import_sets([exercise_set('2026-10-02T17:00:00.0', category='PLANK', duration=60.0)])

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].start_time)
        self.assertEqual(rows[0].exercise_category, 'PLANK')


if __name__ == '__main__':
    unittest.main(verbosity=2)
