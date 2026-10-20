"""Constants for the Shelly BLU TRV Control integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "shelly_blu_trv_control"

CONF_SHELLY_ENTRY_ID = "shelly_entry_id"

# The gateway status is cheap to poll; schedules are only re-read from the
# TRV (via Bluetooth) when its schedule revision counter changes.
SCAN_INTERVAL = timedelta(seconds=60)

# BluTrv.Call is relayed over Bluetooth; the gateway docs state a response
# may take up to one minute.
RPC_TIMEOUT = 75
LOCAL_RPC_TIMEOUT = 15

# After a failed background read of a schedule, wait before trying again
# (doubling up to the maximum). Calls that time out stay queued on the
# gateway, so retrying every poll would block it for all TRVs.
FETCH_BACKOFF = timedelta(minutes=2)
FETCH_BACKOFF_MAX = timedelta(minutes=30)

# Target used when switching from "off" to "heat" without a remembered one.
DEFAULT_HEAT_C = 20.0

BLUTRV_PREFIX = "blutrv:"
GATEWAY_MODEL_PREFIX = "S3GW"

ATTR_RULES = "rules"
ATTR_TIMESPEC = "timespec"
ATTR_TIME = "time"
ATTR_DAYS = "days"
ATTR_ENABLE = "enable"
ATTR_TARGET_C = "target_C"
ATTR_POS = "pos"
ATTR_RULE_ID = "rule_id"
ATTR_SOURCE = "source"

SERVICE_GET_SCHEDULE = "get_schedule"
SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_ADD_RULE = "add_rule"
SERVICE_UPDATE_RULE = "update_rule"
SERVICE_REMOVE_RULE = "remove_rule"
SERVICE_COPY_SCHEDULE = "copy_schedule"
SERVICE_REFRESH = "refresh"

CARD_FILENAME = "shelly-trv-schedule-card.js"
CARD_URL_BASE = f"/{DOMAIN}"
