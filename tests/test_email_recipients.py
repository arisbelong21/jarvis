import unittest

from tests._util import no_audit  # noqa: F401
from plugins import composio_actions as ca

KNOWN = {"arischopper1@gmail.com", "raka.pratama@kantor.co.id"}


class Recipients(unittest.TestCase):
    def test_known_address_no_note(self):
        self.assertEqual(ca.recipient_note({"to": "ArisChopper1@gmail.com"}, KNOWN), "")

    def test_misheard_address_flagged_not_corrected(self):
        note = ca.recipient_note({"to": "arischoper1@gmail.com"}, KNOWN)
        self.assertIn("arischopper1@gmail.com", note)
        self.assertIn("tanyakan", note)

    def test_new_address_flagged(self):
        self.assertIn("alamat baru", ca.recipient_note({"to": "budi@baru.id"}, KNOWN))

    def test_spoken_name_suggestions(self):
        self.assertEqual(ca.name_suggestions("raka", KNOWN), ["raka.pratama@kantor.co.id"])
        self.assertEqual(ca.name_suggestions("zz", KNOWN), [])


if __name__ == "__main__":
    unittest.main()
