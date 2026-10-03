# Copyright (c) 2022, Shridhar Patil and Contributors
# See license.txt

from frappe.tests import UnitTestCase

from frappe_whatsapp.utils.messaging import VALID_CHANNELS


class TestWhatsAppSettings(UnitTestCase):
    def test_supported_channels(self):
        self.assertEqual(VALID_CHANNELS, ("Meta", "Evolution"))
