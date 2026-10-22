import os
import pytest
from dualtrade.credentials import save_local_credentials,load_local_credentials,status
from dualtrade.config import load


@pytest.mark.skipif(os.name!='nt',reason='Windows CurrentUser DPAPI')
def test_dpapi_roundtrip_and_environment_precedence(tmp_path,monkeypatch):
    path=tmp_path/'vault.json';value='test-key-unique-123'
    save_local_credentials({'ALPHAVANTAGE_API_KEY':value,'SEC_USER_AGENT':'Test Üser contact@example.com'},path)
    assert value not in path.read_text()
    monkeypatch.delenv('ALPHAVANTAGE_API_KEY',raising=False);monkeypatch.delenv('SEC_USER_AGENT',raising=False)
    load_local_credentials(path)
    assert os.environ['ALPHAVANTAGE_API_KEY']==value
    assert os.environ['SEC_USER_AGENT']=='Test Üser contact@example.com'
    monkeypatch.setenv('ALPHAVANTAGE_API_KEY','environment-takes-precedence');load_local_credentials(path)
    assert os.environ['ALPHAVANTAGE_API_KEY']=='environment-takes-precedence'


def test_status_reveals_presence_only(monkeypatch):
    monkeypatch.setenv('ALPHAVANTAGE_API_KEY','private-test-value')
    result=status(load())
    assert result['configured']['ALPHAVANTAGE_API_KEY'] is True
    assert 'private-test-value' not in str(result)
