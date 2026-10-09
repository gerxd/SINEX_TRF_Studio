import re

from ..core import logger

COVA_ESTIMATE = ("SOLUTION/MATRIX_ESTIMATE L COVA", "SOLUTION/MATRIX_ESTIMATE U COVA")
RMS_PATTERN = re.compile(
    r"(?:RMS OF UNIT WEIGHT|SIGMA0)[\s:=]*([-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?)", re.IGNORECASE)


def header_rms(lines):
    for line in lines:
        match = RMS_PATTERN.search(line)
        if match:
            try:
                return float(match.group(1).replace("D", "E").replace("d", "e"))
            except ValueError:
                continue
    return None


def non_station_message(counts):
    from ..analysis import datum
    k = sum(counts.values())
    return (f"{k} non-station parameters ({datum.non_station_text(counts)}). "
            "Datum products use the station parameters only. Normal matrix products are not available.")


def unsupported_matrix_blocks(names):
    return [b for b in names
            if (b.startswith(("SOLUTION/MATRIX_ESTIMATE", "SOLUTION/MATRIX_APRIORI"))
                and not b.endswith("COVA"))
            or b.startswith(("SOLUTION/NORMAL_EQUATION_MATRIX", "SOLUTION/NORMAL_EQUATION_VECTOR"))]


def refuse_matrix_blocks(names):
    other = unsupported_matrix_blocks(names)
    if (not any(b in names for b in COVA_ESTIMATE)
            and any(b.startswith(("SOLUTION/MATRIX_ESTIMATE", "SOLUTION/NORMAL_EQUATION_")) for b in other)):
        raise ValueError(
            f"No covariance block. Found {', '.join(other)}, which this version does not read. "
            "Covariance and datum products are not available.")
    for b in other:
        logger.warning(f"{b} is not read by this version.")


def inspect(data, parsers):
    from ..analysis import datum
    meta = data.get("metadata", {})
    names = meta.get("blocks_seen")
    if names is not None:
        refuse_matrix_blocks(names)
        unread = [b for b in names if b not in parsers]
        if unread:
            logger.info("Blocks not read: " + ", ".join(dict.fromkeys(unread)))
    counts = datum.non_station_counts(data["blocks"].get("SOLUTION/ESTIMATE"))
    if counts:
        logger.warning(non_station_message(counts))
    vf = data["blocks"].get("SOLUTION/STATISTICS")
    x = meta.get("header_rms")
    if (isinstance(vf, (int, float)) and x and abs(vf - x) / abs(x) < 0.05
            and abs(x - 1) > 0.1):
        logger.warning(f"Variance factor {vf:g} matches the header RMS of unit weight {x:g}. It may be "
                       "sigma0, not sigma0^2. If so, enter sigma0^2 as a custom variance factor.")
