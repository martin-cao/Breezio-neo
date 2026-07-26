import tempfile
import unittest
from pathlib import Path

from breezio_inference.main import display_name_from_person_id, preload_face_enrollments
from breezio_inference.service import InferenceService


class InferenceMainTest(unittest.TestCase):
    def test_display_name_from_person_id(self):
        self.assertEqual(display_name_from_person_id("alex_chen"), "Alex Chen")
        self.assertEqual(display_name_from_person_id("demo_user"), "Demo User")

    def test_preload_face_enrollments_from_pose_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            person = root / "alex_chen"
            person.mkdir()
            for pose in ("front", "up", "down", "left", "right"):
                (person / f"{pose}.jpg").write_bytes(b"not-a-real-image")

            service = InferenceService()
            enrolled = preload_face_enrollments(service, root)

            self.assertEqual(len(enrolled), 1)
            self.assertEqual(enrolled[0]["person_id"], "alex_chen")
            self.assertEqual(enrolled[0]["display_name"], "Alex Chen")
            self.assertTrue(enrolled[0]["enrolled"])
            self.assertEqual(enrolled[0]["poses"], ["down", "front", "left", "right", "up"])
            self.assertEqual(len(service.list_identities()), 1)


if __name__ == "__main__":
    unittest.main()
