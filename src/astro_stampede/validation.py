"""Ordered diagnostics for local input validation."""


class ValidationError(ValueError):
    def __init__(self, problems):
        self.problems = tuple(problems)
        super().__init__("\n".join(self.problems))


def report(exc):
    problems = exc.problems if isinstance(exc, ValidationError) else (str(exc),)
    return f"{len(problems)} problem(s) found:\n" + "\n".join(f"  - {p}" for p in problems)
