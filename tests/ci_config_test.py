import pytest

# add a dummy test for ci config
def test_capital_case():
    assert ('transcriptomics').capitalize() == 'Transcriptomics'
