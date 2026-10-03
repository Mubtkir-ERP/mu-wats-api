"""Core routing/normalisation tests for WhatsApp Message."""

from frappe.tests import UnitTestCase

from frappe_whatsapp.utils.messaging import (
    can_advance_status,
    normalize_number,
    normalise_message_status,
)


class TestWhatsAppMessage(UnitTestCase):
    def test_number_normalisation(self):
        self.assertEqual(normalize_number("+966 50 123 4567"), "966501234567")
        self.assertEqual(normalize_number("05-0123-4567"), "966501234567")
        self.assertEqual(normalize_number("00971501234567"), "971501234567")

    def test_status_normalisation(self):
        self.assertEqual(normalise_message_status("sent"), "Sent")
        self.assertEqual(normalise_message_status("DELIVERED"), "Delivered")
        self.assertEqual(normalise_message_status(3), "Read")
        self.assertEqual(normalise_message_status("failed"), "Failed")

    def test_status_does_not_downgrade(self):
        self.assertFalse(can_advance_status("Read", "Delivered"))
        self.assertFalse(can_advance_status("Delivered", "Sent"))
        self.assertTrue(can_advance_status("Sent", "Delivered"))
        self.assertTrue(can_advance_status("Delivered", "Read"))
