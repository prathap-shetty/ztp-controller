class ZtpError(Exception):
    def __init__(self, code: str, status: int, message: str):
        self.code, self.status, self.message = code, status, message
        super().__init__(message)


class InventoryDenied(ZtpError):
    def __init__(self):
        super().__init__("not_eligible", 403, "Device is not eligible for provisioning")


class InventoryUnavailable(ZtpError):
    def __init__(self):
        super().__init__("inventory_unavailable", 503, "Inventory is unavailable")


class InvalidIntent(ZtpError):
    def __init__(self):
        super().__init__("invalid_intent", 403, "Device is not eligible for provisioning")
