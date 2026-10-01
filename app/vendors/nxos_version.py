import re


def same_nxos_release(left, right):
    """Accept the NX-OS 10.5 maintenance suffix omitted by show version."""

    def canonical(value):
        return re.sub(r"^(10\.5\([0-9]+\))M$", r"\1", value.strip())

    return canonical(left) == canonical(right)


def version_satisfies(current, target, allow_newer=False):
    if same_nxos_release(current, target):
        return True
    if not allow_newer:
        return False

    # Compare numeric NX-OS releases only; unknown suffixes are not ordered.
    def parts(value):
        match = re.fullmatch(r"(\d+)\.(\d+)\((\d+)\)(?:M)?", value.strip())
        return tuple(map(int, match.groups())) if match else None

    actual, desired = parts(current), parts(target)
    if actual is None or desired is None:
        raise ValueError(
            "Cannot order this NX-OS release; use an explicit supported release policy"
        )
    return actual > desired
