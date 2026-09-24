# io/reader.py
import time
from pathlib import Path
from typing import Dict

from ..core import logger, benchmark
from .parsers import SinexBlockParser, MatrixEstimateParser
from . import parallel


class SinexStructureError(ValueError):
    pass


def parse_sinex_file(filename, block_parsers, skip_epochs_block=False,
                     skip_validation=False, parse_generation=None, check_structure=False):
    start_time = time.time()
    logger.info(f"Starting parse of file: {filename.name}")
    gen = parse_generation
    parse_token = benchmark.begin('parse file (all blocks)', gen)
    stream_token = None

    sinex_data = {
        'header': [],
        'blocks': {},
        'metadata': {'filename': filename.name}
    }
    cur_block = None
    cur_block_data = []
    total_lines = 0
    skipping_epoch = False
    statistics = None
    # streaming state for matrix blocks
    streaming_parser = None
    open_blocks = []
    executor = None

    try:
        with open(filename, 'rb') as file:
            while line := file.readline():
                total_lines += 1
                ln = line.decode('utf-8', errors='replace').rstrip()

                if len(ln) == 0:
                    continue

                if ln[0].startswith('*'):
                    sinex_data['header'].append(ln)
                elif ln.startswith('+'):
                    cur_block = ln[1:].strip()
                    cur_block_data = []
                    streaming_parser = None
                    open_blocks.append(cur_block)

                    if cur_block in ['SOLUTION/EPOCHS']:
                        if skip_epochs_block: 
                            skipping_epoch = True
                            logger.info(f"Skipping SOLUTION/EPOCHS block")
                        else:
                            skipping_epoch = False
                            logger.info(f"Parsing block: {cur_block}")

                    if cur_block in block_parsers:
                        parser = block_parsers[cur_block]
                        if isinstance(parser, MatrixEstimateParser):
                            # find matrix dim from SOLUTION/ESTIMATE
                            est = sinex_data['blocks'].get('SOLUTION/ESTIMATE')
                            skipped = getattr(block_parsers.get('SOLUTION/ESTIMATE'), 'skipped', 0)
                            if est is not None and skipped:
                                raise ValueError(
                                    f"{skipped} SOLUTION/ESTIMATE line{'s' if skipped != 1 else ''} "
                                    "could not be read, so the covariance cannot be matched to the parameters.")
                            if est is not None:
                                sz = len(est)
                                parser.init_stream(sz)
                                streaming_parser = parser
                                stream_token = benchmark.begin(f'stream {cur_block}', gen)
                                logger.info(f"Stream-parsing {cur_block} ({sz}x{sz})")
                                block_start = file.tell()
                                large = parallel.is_large(file, block_start)
                                file.seek(block_start)
                                if large and parallel.ENABLED:
                                    if executor is None:
                                        executor = parallel.make_executor()
                                    lines, physical, fortran = parallel.stream_block(
                                        file, filename, parser._matrix, sinex_data['header'], executor)
                                    parser._stream_lines += lines
                                    total_lines += physical
                                    if fortran:
                                        logger.info("File contains FORTRAN scientific notation")
                            else:
                                streaming_parser = None

                elif ln.startswith('-'):
                    logger.info(f"End of reading block: {cur_block}")
                    if check_structure and not open_blocks:
                        logger.error(f"Unmatched block end at line {total_lines}")
                        raise SinexStructureError("Invalid SINEX block structure!")
                    if open_blocks:
                        open_blocks.pop()

                    if streaming_parser is not None and streaming_parser.is_streaming:
                        parsed = streaming_parser.finalize_stream()
                        sinex_data['blocks'][cur_block] = parsed
                        benchmark.end(stream_token)
                        stream_token = None
                        streaming_parser = None

                    elif cur_block in block_parsers and len(cur_block_data) > 0:
                        parser = block_parsers[cur_block]
                        if skip_validation or parser.validate(cur_block_data):
                            parsed = parser.parse(cur_block_data)
                            sinex_data['blocks'][cur_block] = parsed
                            logger.info(f"Parsed {cur_block} with {len(cur_block_data)} lines.")
                            if cur_block == 'SOLUTION/STATISTICS':
                                statistics = cur_block_data

                                logger.info(f"Values read:\n{cur_block_data}")
                            else:
                                pass
                        else:
                            logger.debug(f"Couldn't parse block: {cur_block}")
                    else:
                        logger.debug(f"{cur_block} is not a valid block")
                    cur_block = None
                    cur_block_data = []
                    skipping_epoch = False
                elif cur_block:
                    if not skipping_epoch:
                        if streaming_parser is not None:
                        #stream into numpy array
                            streaming_parser.feed_line(ln)
                        else:
                            cur_block_data.append(ln)
        if check_structure and open_blocks:
            logger.error("Unclosed blocks: " + ", ".join(open_blocks))
            raise SinexStructureError("Invalid SINEX block structure!")
        est = sinex_data['blocks'].get('SOLUTION/ESTIMATE')
        if est and any(p.get('index') != i for i, p in enumerate(est, 1)):
            logger.warning("SOLUTION/ESTIMATE is not in INDEX order 1 to n. The datum and normal computations sort it by INDEX, or refuse it if INDEX is not a permutation of 1 to n.")
        logger.info(f"Finished parse of {filename.name} in {time.time()-start_time:.3f}s.")
        logger.info(f"Total lines read: {total_lines}, blocks: {len(sinex_data['blocks'])}.")
        benchmark.end(parse_token)
        return sinex_data

    except Exception:
        benchmark.cancel(stream_token)
        benchmark.cancel(parse_token)
        logger.exception("Parsing error")
        raise
    finally:
        if executor is not None:
            executor.terminate()
