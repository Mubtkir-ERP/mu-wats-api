"""Explicit single-attempt Meta transport; send retries belong to the journal."""

import requests

from frappe_whatsapp.utils.delivery_guard import DeliveryRejected, REJECTED_HTTP_STATUSES


def make_post_request(url, headers, data):
    # requests' default adapter does not retry POST. Do not install a retrying
    # adapter here: a lost response cannot establish that Meta rejected a send.
    response = requests.post(url, headers=headers, data=data, timeout=30)
    if response.status_code in REJECTED_HTTP_STATUSES:
        raise DeliveryRejected("Meta rejected the request: " + response.text[:1000])
    response.raise_for_status()
    return response.json()
