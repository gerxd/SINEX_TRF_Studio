# io/parsers.py
from abc import ABC, abstractmethod
import re
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Any
from ..core import logger

###############################################################################
# 1) Abstract Parser
###############################################################################
class SinexBlockParser(ABC):
    @abstractmethod
    def parse(self, block_data: List[str]) -> Any:
        pass

    @abstractmethod
    def validate(self, block_data: List[str]) -> bool:
        pass

    @abstractmethod
    def export(self, data: Any, filename: Path, format: str):
        pass


def plain_records(df):
    columns = []
    for name in df.columns:
        values = df[name].to_numpy()
        if values.dtype.kind not in 'biuf':
            values = np.array(['' if v is None else str(v) for v in values], dtype=str)
        columns.append((str(name), values))
    out = np.empty(len(df), dtype=[(name, values.dtype) for name, values in columns])
    for name, values in columns:
        out[name] = values
    return out

###############################################################################
# 2) Parser Classes
###############################################################################
class SiteIDParser(SinexBlockParser):
    def validate(self, block_data: List[str]) -> bool:
        return bool(block_data)

    def parse(self, block_data: List[str]) -> List[dict]: 
        stations = []
        for line in block_data:
            ln = line.rstrip()
            if not ln.strip() or ln.lstrip().startswith('*'):
                continue

            if len(ln) < 60: #standard sinex limit
                logger.warning(f"Skipping short SITE/ID line: {ln}")
                continue

            try:
                code = ln[0:5].strip()  # 2-5 station code
                #point = ln[6:7].strip() unused and leaves memory footprint
                domes = ln[9:18].strip()  #10-18: DOMES number
                coord_part = ln[43:].strip()  # all else after description
                coord_tokens = coord_part.split()

                if len(coord_tokens) < 7:  # Need at least lon_d lon_m lon_s lat_d lat_m lat_s height
                    logger.warning(f"Insufficient coordinate tokens in: {ln}")
                    continue
                # extract coordinates (last 7: lon_d lon_m lon_s lat_d lat_m lat_s height)
                lon_d, lon_m, lon_s = coord_tokens[-7], coord_tokens[-6], coord_tokens[-5]
                lat_d, lat_m, lat_s = coord_tokens[-4], coord_tokens[-3], coord_tokens[-2]
                height = float(coord_tokens[-1])
                # DMS to decimal conversion
                longitude = self.dms_to_decimal(lon_d, lon_m, lon_s, is_lat=False)
                latitude = self.dms_to_decimal(lat_d, lat_m, lat_s, is_lat=True)
                if abs(longitude) > 180 or abs(latitude) > 90:
                    logger.warning(f"Station {code} has invalid coordinates: lat={latitude}, lon={longitude}")
                    continue

                stations.append({
                    'code': code,
                    'domes': domes,
                    'latitude': latitude,
                    'longitude': longitude,
                    'height': height
                })

            except (ValueError, IndexError) as e:
                logger.warning(f"Error parsing station line: {str(e)} - {ln}")
                continue

        logger.info(f"Successfully parsed {len(stations)} stations")
        return stations

    def dms_to_decimal(self, d_str, m_str, s_str, is_lat=False):
        try:
            deg = float(d_str)
            mn = float(m_str)
            sc = float(s_str)
        except ValueError:
            logger.warning(f"Failed to parse DMS: {d_str}, {m_str}, {s_str}")
            return 0.0
        # conv to decimal
        decimal = abs(deg) + mn / 60.0 + sc / 3600.0
    #handle negative coordinates
        if d_str.strip().startswith('-'):
            decimal = -decimal
        elif not is_lat and decimal > 180.0:
            # conv longitude > 180 to negative (western hemisphere)
            decimal = decimal - 360.0

        return decimal

    def export(self, data: List[dict], filename: Path, format: str):
        df = pd.DataFrame(data)
        if format == 'xlsx':
            df.to_excel(filename, index=False)
        elif format == 'csv':
            df.to_csv(filename, index=False)
        elif format == 'txt':
            df.to_csv(filename, index=False, sep='\t')
        elif format == 'npy':
            np.save(filename, plain_records(df))
        else:
            raise ValueError(f"Unsupported export: {format}")
        logger.info(f"Exported SITE/ID data => {filename}")

class MatrixEstimateParser(SinexBlockParser):
    def __init__(self):
        # Streaming state
        self._matrix = None
        self._should_replace = None
        self._stream_lines = 0

    @property
    def is_streaming(self) -> bool:
        return self._matrix is not None

    def validate(self, block_data: List[str]) -> bool:
        for line in block_data:
            ln = line.strip()
            if not ln or ln[0] in ('%',"*"):
                continue
            tokens = ln.split()
            if ln.startswith('*'):
                if len(tokens) < 3:
                    return False
            else:
                if len(tokens) < 3:
                    return False
        return True
#####################################
#old validation
    # def validate(self, block_data: List[str]) -> bool:
    #     return len(block_data) > 0  # Simple existence check, let parse() handle malformed data
#####################################

    # ---- Streaming interface (single-pass, no buffering) ----
    def init_stream(self, size: int, path=None):
        # Allocate the target matrix up front; lines are fed directly into it
        if path is None:
            self._matrix = np.zeros((size, size), dtype=float)
        else:
            self._matrix = np.lib.format.open_memmap(path, mode="w+", dtype=float, shape=(size, size))
        self._should_replace = None
        self._stream_lines = 0

    def feed_line(self, ln: str):
        # Parse a single data line directly into the pre-allocated matrix
        ln = ln.strip()
        if not ln or ln.startswith('%'):
            return

        tokens = ln.split()
        if len(tokens) < 3:
            return

        row_idx = int(tokens[0]) - 1
        col_idx = int(tokens[1]) - 1
        self._stream_lines += 1

        # recognize fortran notation on the first data line
        if self._should_replace is None:
            self._should_replace = any('d' in s or 'D' in s for s in tokens[2:])
            if self._should_replace:
                logger.info("File contains FORTRAN scientific notation")

        if self._should_replace:
            vals = [float(v.replace('D', 'E').replace('d', 'e')) for v in tokens[2:]]
        else:
            try:
                vals = [float(v) for v in tokens[2:]]
            except ValueError:
                vals = [float(v.replace('D', 'E').replace('d', 'e')) for v in tokens[2:]]

        for i, val in enumerate(vals):
            cc = col_idx + i
            self._matrix[row_idx, cc] = val
            self._matrix[cc, row_idx] = val

    def finalize_stream(self) -> np.ndarray:
        # Return the completed matrix and reset streaming state
        m = self._matrix
        count = self._stream_lines
        self._matrix = None
        self._should_replace = None
        self._stream_lines = 0
        logger.info(f"Stream-parsed matrix ({m.shape[0]}x{m.shape[1]}) from {count} lines.")
        return m

    # ---- Legacy batch interface (kept for non-streaming callers) ----
    def parse(self, block_data: List[str]) -> np.ndarray:
        line = block_data[-1]
        sz = int(line.split()[0])

        matrix = np.zeros((sz, sz), dtype=float)

        should_replace = False
        first_line = True

        for line in block_data:
            ln = line.strip()
            if not ln or ln.startswith('%'):
                continue

            tokens = ln.split()

            rnum = int(tokens[0])
            cnum = int(tokens[1])


            if first_line:
                should_replace = any('d' in s or 'D' in s for s in tokens[2:])
                if should_replace:
                    logger.info("File contains FORTRAN scientific notation")
                first_line = False

            if should_replace:
                vals = [float(v.replace('D','E').replace('d','e')) for v in tokens[2:]]
            else:
                try:
                    vals = [float(v) for v in tokens[2:]]
                except ValueError:
                    vals = [float(v.replace('D','E').replace('d','e')) for v in tokens[2:]]

            row_idx = rnum - 1
            col_idx = cnum - 1

            for i, val in enumerate(vals):
                # So here the program determines the actual column
                # i takes the values {0, 1, .., size(vlist) - 1}
                # In this case vlist is at most 3 numbers
                cc = col_idx + i
                #if cc <= row_idx:
                matrix[row_idx, cc] = val
                # if row_idx != cc: -> make the matrix symetric
                matrix[cc, row_idx] = val

        return matrix

    def export(self, data: np.ndarray, filename: Path, format: str):
        if format == 'xlsx':
            pd.DataFrame(data).to_excel(filename, index=False, header=False)
        elif format == 'csv':
            if np.isnan(data).any():
                pd.DataFrame(data).to_csv(filename, index=False, header=False, float_format='%.17g')
            else:
                np.savetxt(filename, data, fmt='%.17g', delimiter=',')
        elif format == 'txt':
            np.savetxt(filename, data, fmt='%.17g', delimiter='\t')
        elif format == 'npy':
            np.save(filename, data)
        else:
            raise ValueError(f"Unsupported export format: {format}")
        logger.info(f"Exported matrix => {filename}")
        #notification.notify(
        #    title="SINEX Studio",
        #    message=f"File: {filename} exported as {format}"
        #)

class SolutionStatisticsParser(SinexBlockParser):
   #variance factor string finder
    def validate(self, block_data: List[str]) -> bool:
        return any("variance factor" in ln.lower() for ln in block_data)

    def parse(self, block_data: List[str]) -> float:
        found = None
        for line in block_data:
            ll = line.lower()
            if "variance factor" in ll:
                lconv = line.replace('D','E').replace('d','e')
                tokens = lconv.split()
                for tk in reversed(tokens):
                    try:
                        val = float(tk)
                        found = val
                        break
                    except ValueError:
                        pass
        if found is None:
            raise ValueError("No variance factor found in SOLUTION/STATISTICS.")
        return found

    def export(self, data: float, filename: Path, format: str):
        if format == 'xlsx':
            pd.DataFrame([[float(data)]], index=['VARIANCE FACTOR']).to_excel(
                filename, header=False)
        elif format == 'npy':
            np.save(filename, np.array(float(data)))
        else:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"VARIANCE FACTOR: {data}\n")
        logger.info(f"Exported variance factor => {filename}")

class ParameterParser(SinexBlockParser):
    def validate(self, block_data: List[str]) -> bool:
        for ln in block_data:
            line = ln.strip()
            if not line or line.startswith('*'):
                continue
            tokens = line.split()
            if len(tokens) < 9:
                return False
        return True

    _COLUMNS = ((1, 6), (7, 13), (14, 18), (19, 21), (22, 26), (27, 39), (40, 44), (47, 68), (69, 80))
    _GAPS = (6, 13, 18, 21, 26, 39, 44, 46, 68)

    @classmethod
    def _parse_fixed_columns(cls, line: str) -> Optional[dict]:
        if len(line) < 70 or any(line[i] != ' ' for i in cls._GAPS):
            return None
        idx, ptype, code, pt, soln, epoch, unit, val, sig = (line[a:b].strip() for a, b in cls._COLUMNS)
        if not ptype or any(' ' in s for s in (ptype, code, pt, soln, epoch, unit)):
            return None
        try:
            return {
                'index': int(idx),
                'type': ptype,
                'code': code,
                'pt': pt,
                'soln': int(soln) if soln.isdigit() else None,
                'epoch': epoch,
                'unit': unit,
                'value': float(val.replace('D', 'E').replace('d', 'e')),
                'sigma': float(sig.replace('D', 'E').replace('d', 'e')),
            }
        except ValueError:
            return None

    def parse(self, block_data: List[str]) -> List[dict]:
        results = []
        irregular = 0
        unreadable = 0
        fixed = 0
        skipped = 0
        for line in block_data:
            ln = line.strip()
            if not ln or ln.startswith('*'):
                continue
            tokens = ln.split()
            record = None
            if len(tokens) != 10:
                irregular += 1
            else:
                try:
                    idx = int(tokens[0])
                except:
                    idx = -1
                soln = None
                try:
                    soln = int(tokens[4])
                except Exception:
                    soln = None
                try:
                    record = {
                        'index': idx,
                        'type': tokens[1],
                        'code': tokens[2],
                        'pt': tokens[3],
                        'soln': soln,
                        'epoch': tokens[5],
                        'unit': tokens[6],
                        'value': float(tokens[8].replace('D', 'E').replace('d', 'e')),
                        'sigma': float(tokens[9].replace('D', 'E').replace('d', 'e')),
                    }
                except ValueError:
                    unreadable += 1
            if record is None:
                record = self._parse_fixed_columns(line)
                if record is None:
                    skipped += 1
                    continue
                fixed += 1
            results.append(record)
        self.skipped = skipped
        if irregular or unreadable:
            logger.warning(
                f"{irregular} parameter lines do not have 10 fields and {unreadable} have a "
                f"number that cannot be read. {fixed} were read by the SINEX fixed columns "
                f"and {skipped} were skipped.")
        return results

    def export(self, data: List[dict], filename: Path, format: str):
        df = pd.DataFrame(data)
        if format == 'xlsx':
            df.to_excel(filename, index=False)
        elif format == 'csv':
            df.to_csv(filename, index=False)
        elif format == 'txt':
            df.to_csv(filename, index=False, sep='\t')
        elif format == 'npy':
            np.save(filename, plain_records(df))
        else:
            raise ValueError(f"Unsupported export format: {format}")
        logger.info(f"Exported param data => {filename}")

class TableParser(SinexBlockParser):
    def validate(self, block_data: List[str]) -> bool:
        return bool(block_data)

    def export(self, data: List[dict], filename: Path, format: str):
        df = pd.DataFrame(data)
        if format == 'xlsx':
            df.to_excel(filename, index=False)
        elif format == 'csv':
            df.to_csv(filename, index=False)
        elif format == 'txt':
            df.to_csv(filename, index=False, sep='\t')
        elif format == 'npy':
            np.save(filename, plain_records(df))
        else:
            raise ValueError(f"Unsupported export format: {format}")
        logger.info(f"Exported table => {filename}")


def _soln(text):
    return int(text) if text.isdigit() else None


class EpochsParser(TableParser):
    def parse(self, block_data: List[str]) -> List[dict]:
        rows = []
        for line in block_data:
            ln = line.strip()
            if not ln or ln.startswith('*'):
                continue
            t = ln.split()
            if len(t) < 7:
                logger.warning(f"Skipping SOLUTION/EPOCHS line: {ln}")
                continue
            rows.append({'code': t[0], 'pt': t[1], 'soln': _soln(t[2]), 'obs_code': t[3],
                         'data_start': t[4], 'data_end': t[5], 'mean_epoch': t[6]})
        return rows


class DiscontinuityParser(TableParser):
    def parse(self, block_data: List[str]) -> List[dict]:
        rows = []
        for line in block_data:
            ln = line.strip()
            if not ln or ln.startswith('*'):
                continue
            t = ln.split()
            if len(t) < 7 or t[6] not in ('P', 'V'):
                logger.warning(f"Skipping SOLUTION/DISCONTINUITY line: {ln}")
                continue
            rest = t[7:]
            if rest and rest[0] == '-':
                rest = rest[1:]
            rows.append({'code': t[0], 'pt': t[1], 'soln': _soln(t[2]), 'type': t[6],
                         'start': t[4], 'end': t[5],
                         'reason': ' '.join(rest).replace('_', ' ')})
        return rows

def create_parsers():
    #creates parser instances
    return {
        'SOLUTION/MATRIX_ESTIMATE L COVA': MatrixEstimateParser(),
        'SOLUTION/MATRIX_APRIORI L COVA': MatrixEstimateParser(),
        'SOLUTION/MATRIX_ESTIMATE U COVA': MatrixEstimateParser(),
        'SOLUTION/MATRIX_APRIORI U COVA': MatrixEstimateParser(),
        'SITE/ID': SiteIDParser(),
        'SOLUTION/STATISTICS': SolutionStatisticsParser(),
        'SOLUTION/ESTIMATE': ParameterParser(),
        'SOLUTION/APRIORI': ParameterParser(),
        'SOLUTION/EPOCHS': EpochsParser()
    }
