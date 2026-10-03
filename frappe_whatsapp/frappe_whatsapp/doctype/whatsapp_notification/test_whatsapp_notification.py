# Copyright (c) 2022, Shridhar Patil and Contributors
# See license.txt

from frappe.tests import UnitTestCase

from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_notification.whatsapp_notification import WhatsAppNotification


class TestWhatsAppNotification(UnitTestCase):
    def test_attachment_content_type(self):
        self.assertEqual(WhatsAppNotification._attachment_content_type("x.png"), "image")
        self.assertEqual(WhatsAppNotification._attachment_content_type("x.mp4"), "video")
        self.assertEqual(WhatsAppNotification._attachment_content_type("x.mp3"), "audio")
        self.assertEqual(WhatsAppNotification._attachment_content_type("x.pdf"), "document")
