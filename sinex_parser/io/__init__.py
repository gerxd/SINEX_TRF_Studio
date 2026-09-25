# io/__init__.py
from .parsers import SinexBlockParser, SiteIDParser, MatrixEstimateParser
from .parsers import SolutionStatisticsParser, ParameterParser, EpochsParser

def create_parsers():
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
