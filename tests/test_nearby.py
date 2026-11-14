from flats import nearby


def test_nearby_filters_sorts_and_limits(monkeypatch):
    places = [{'name': str(i), 'category': 'Продукты', 'lat': 43 + i * .001, 'lon': 132, 'url': 'https://www.openstreetmap.org/node/1'} for i in (4, 2, 1, 3, 20)]
    monkeypatch.setattr(nearby, '_snapshot', lambda: {'places': places})
    groups = nearby.nearby_places({'lat': 43, 'lon': 132})
    assert [p['name'] for p in groups[0]['places']] == ['1', '2', '3']
    assert 100 < groups[0]['places'][0]['distance'] < 120
    assert nearby.nearby_places(None) == []
