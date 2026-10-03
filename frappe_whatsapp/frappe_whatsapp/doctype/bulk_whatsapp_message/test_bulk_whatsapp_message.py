# Copyright (c) 2025, Shridhar Patil and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

from frappe_whatsapp.frappe_whatsapp.doctype.bulk_whatsapp_message.bulk_whatsapp_message import _media_kind


class TestBulkWhatsAppMessage(FrappeTestCase):
    def test_media_kind(self):
        self.assertEqual(_media_kind("photo.jpg"), ("image", None))
        self.assertEqual(_media_kind("voice.ogg"), ("audio", None))
        self.assertEqual(_media_kind("invoice.pdf"), ("document", "application/pdf"))
        self.assertEqual(_media_kind("unknown.bin"), ("document", "application/octet-stream"))
