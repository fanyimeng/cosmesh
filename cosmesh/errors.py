class Error(Exception):
    def __init__(self, code, message, hint=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self):
        return {"code": self.code, "message": self.message, "hint": self.hint}
