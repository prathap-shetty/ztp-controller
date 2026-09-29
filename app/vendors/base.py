from abc import ABC, abstractmethod

from app.models.contracts import DeviceIntent, Manifest, ObservedDevice


class ZtpVendorAdapter(ABC):
    @abstractmethod
    def build_manifest(self, intent: DeviceIntent, observed: ObservedDevice) -> Manifest: ...

    @abstractmethod
    def bootstrap_type(self) -> str: ...
