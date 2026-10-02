from app.errors import InventoryDenied
from app.models.contracts import DeviceIdentity, DeviceIntent, ObservedDevice


def authorize_identity(device: DeviceIdentity, observed: ObservedDevice) -> None:
    for field, expected, code, message in [
        ("status", "staged", "device_not_staged", "Inventory device must be staged"),
        (
            "serial_number",
            observed.serial_number,
            "serial_mismatch",
            "Chassis serial does not match inventory",
        ),
        ("vendor", observed.vendor, "vendor_mismatch", "Device manufacturer does not match"),
        (
            "platform",
            observed.platform,
            "platform_mismatch",
            "Device platform does not match NX-OS",
        ),
        (
            "model",
            observed.model,
            "model_mismatch",
            "Chassis model does not match inventory device type",
        ),
    ]:
        if getattr(device, field) != expected:
            raise InventoryDenied(code, message)


def authorize_intent(intent: DeviceIntent, observed: ObservedDevice) -> None:
    authorize_identity(intent.device, observed)
    if intent.ztp_enabled is not True:
        raise InventoryDenied("ztp_disabled", "ZTP is not enabled for this device")
