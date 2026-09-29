from app.errors import InventoryDenied
from app.models.contracts import DeviceIdentity, DeviceIntent, ObservedDevice


def authorize_identity(device: DeviceIdentity, observed: ObservedDevice) -> None:
    if (
        device.status != "staged"
        or device.serial_number != observed.serial_number
        or device.vendor != observed.vendor
        or device.platform != observed.platform
        or device.model != observed.model
    ):
        raise InventoryDenied()


def authorize_intent(intent: DeviceIntent, observed: ObservedDevice) -> None:
    authorize_identity(intent.device, observed)
    if intent.ztp_enabled is not True:
        raise InventoryDenied()
