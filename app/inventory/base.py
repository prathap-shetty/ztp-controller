from abc import ABC, abstractmethod

from app.models.contracts import DeviceIdentity, DeviceIntent, ManagementAddress


class InventoryProvider(ABC):
    @abstractmethod
    def get_device_by_serial(self, serial: str) -> DeviceIdentity: ...

    @abstractmethod
    def get_device_intent(self, device_id: str) -> DeviceIntent: ...

    def get_management_ip(self, device_id: str) -> ManagementAddress:
        return self.get_device_intent(device_id).management

    def update_device_status(self, device_id: str, status: str) -> None:
        raise NotImplementedError("Inventory writes are disabled in milestone one")

    @abstractmethod
    def check_ready(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
