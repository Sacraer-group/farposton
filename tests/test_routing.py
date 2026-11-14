from types import SimpleNamespace

import pytest
import requests

from flats import routing


class HTTP:
    def __init__(self,data,status=200): self.data,self.status=data,status; self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return SimpleNamespace(status_code=self.status,json=lambda:self.data)
    get=post


@pytest.fixture
def key(tmp_path,monkeypatch):
    p=tmp_path/'key';p.write_text('private-key')
    monkeypatch.setenv('FLATS_2GIS_KEY_FILE',str(p))


def test_routes_extract_alternatives_and_transfer_types(key):
    http=HTTP([dict(total_duration=100,transfer_count=1,crossing_count=2,pedestrian=False,total_walkway_distance='пешком 2 мин'),
               dict(total_duration=200,transfer_count=0,crossing_count=0,pedestrian=True)])
    result=routing.route({'lat':43,'lon':131},{'lat':43.1,'lon':131},123,http=http)
    assert result['status']=='ok'
    assert result['alternatives'][0]['transfers']==3
    assert result['alternatives'][1]['pedestrian'] is True
    assert http.calls[0][1]['json']['start_time']==123
    assert http.calls[0][1]['json']['enable_schedule'] is True
    assert 'key' not in result


@pytest.mark.parametrize('status,expected',[(204,'no_route'),(403,'api_error'),(429,'api_error')])
def test_http_errors_are_sanitized(key,status,expected):
    assert routing.route({'lat':43,'lon':131},{'lat':43.1,'lon':131},1,http=HTTP([],status))['status']==expected


def test_missing_key_is_not_exposed(monkeypatch):
    monkeypatch.delenv('FLATS_2GIS_KEY_FILE',raising=False)
    assert routing.route({}, {}, 1)['status']=='api_error'


def test_places_checks_provider_code_even_with_http200(key):
    with pytest.raises(RuntimeError,match='не имеет доступа'):
        routing.places('ДВФУ',http=HTTP({'meta':{'code':403}}))


@pytest.mark.parametrize('lat,lon',[(True,131),(float('nan'),131),(43,181),(None,131)])
def test_coordinates_validated(lat,lon):
    with pytest.raises(ValueError):routing.point({'lat':lat,'lon':lon})
