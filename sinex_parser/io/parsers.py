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
        if deg < 0:
            decimal = -decimal
        elif not is_lat and deg > 180.0:
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
            np.save(filename, df.to_records(index=False))
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
    def init_stream(self, size: int):
        # Allocate the target matrix up front; lines are fed directly into it
        self._matrix = np.zeros((size, size), dtype=float)
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
            vals = [float(v) for v in tokens[2:]]

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
                vals = [float(v) for v in tokens[2:]]

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
        df = pd.DataFrame(data)
        if format == 'xlsx':
            df.to_excel(filename, index=False, header=False)
        elif format == 'csv':
            df.to_csv(filename, index=False, header=False, float_format='%.17g')
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

    def parse(self, block_data: List[str]) -> List[dict]:
        results = []
        for line in block_data:
            ln = line.strip()
            if not ln or ln.startswith('*'):
                continue
            tokens = ln.split()
            if len(tokens) < 9:
                continue
            try:
                idx = int(tokens[0])
            except:
                idx = -1
            param_type = tokens[1]
            # extract station code as token 2 (standard structure)
            station_code = tokens[2] #if len(tokens) > 2 else "" sanity check
            pt = tokens[3] if len(tokens) > 3 else ""
            soln = None
            if len(tokens) > 4:
                try:
                    soln = int(tokens[4])
                except Exception:
                    soln = None

            epoch = tokens[5] if len(tokens) > 5 else ""
            unit = tokens[6] if len(tokens) > 6 else ""

            # fortran "D" notation handling
            val_str = tokens[8].replace('D', 'E').replace('d', 'e')
            sig_str = "0"
            if len(tokens) >= 10:
                sig_str = tokens[9].replace('D', 'E').replace('d', 'e')
            try:
                val_f = float(val_str)
            except:
                val_f = 0.0
            try:
                sig_f = float(sig_str)
            except:
                sig_f = 0.0
            results.append({
                'index': idx, #index
                'type': param_type,
                'code': station_code,
                'pt': pt,
                'soln': soln,
                'epoch': epoch,
                'unit': unit,
                'value': val_f,
                'sigma': sig_f,
            })
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
            np.save(filename, df.to_records(index=False))
        else:
            raise ValueError(f"Unsupported export format: {format}")
        logger.info(f"Exported param data => {filename}")

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
        'SOLUTION/APRIORI': ParameterParser()
    }
