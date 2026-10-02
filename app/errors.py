class ZtpError(Exception):
    def __init__(self, code: str, status: int, message: str):
        self.code, self.status, self.message = code, status, message
        super().__init__(message)


class InventoryDenied(ZtpError):
    def __init__(self, code="not_eligible", message="Device is not eligible for provisioning"):
        super().__init__(code, 403, message)


class InventoryUnavailable(ZtpError):
    def __init__(self):
        super().__init__("inventory_unavailable", 503, "Inventory is unavailable")


class InvalidIntent(ZtpError):
    def __init__(self, code="invalid_intent", message="Device is not eligible for provisioning"):
        super().__init__(code, 403, message)
