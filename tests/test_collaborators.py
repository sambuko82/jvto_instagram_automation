"""Which collaborator handles survive when Instagram refuses some of them."""

import json

import composio

from jvto_instagram_automation.composio_publisher import ComposioPublisher

REJECTED = 'Cannot load user with a private profile or invalid'


class _FakeProxy:
    """Accepts only the handles it was told are real."""

    def __init__(self, good):
        self.good = set(good)
        self.calls = []

    def proxy(self, *, endpoint, method, body, connected_account_id):
        tags = json.loads(body['collaborators']) if 'collaborators' in body else []
        self.calls.append(tags)

        class R:
            pass

        r = R()
        bad = [t for t in tags if t not in self.good]
        r.data = {'error': {'message': REJECTED}} if bad else {'id': 'container-1'}
        return r


def _publisher(monkeypatch, good):
    fake = _FakeProxy(good)

    tools = type('Tools', (), {'proxy': staticmethod(fake.proxy)})()
    client = type('Client', (), {'tools': tools})()
    monkeypatch.setattr(
        composio, 'Composio', lambda api_key=None: type('C', (), {'client': client})()
    )

    p = ComposioPublisher('key', 'jvto_automation')
    monkeypatch.setattr(p, '_connected_account_id', lambda: 'ca_1')
    return p, fake


def test_a_stale_handle_does_not_cost_the_others_their_credit(monkeypatch):
    p, proxy = _publisher(monkeypatch, good={'kiki.the.explorer', 'gfranosept'})

    container, rejected = p._create_carousel_container(
        'ig-1', 'cap', ['c1', 'c2'], ['kiki.the.explorer', 'trisbalii', 'gfranosept']
    )

    assert container['id'] == 'container-1'
    assert rejected == ['trisbalii']
    # The surviving handles are still tagged, which is the whole point.
    assert proxy.calls[-1] == ['kiki.the.explorer', 'gfranosept']


def test_every_handle_bad_still_publishes_untagged(monkeypatch):
    p, proxy = _publisher(monkeypatch, good=set())

    container, rejected = p._create_carousel_container('ig-1', 'cap', ['c1'], ['gone', 'also_gone'])

    assert container['id'] == 'container-1'
    assert rejected == ['gone', 'also_gone']
    assert proxy.calls[-1] == []


def test_all_good_handles_cost_no_extra_calls(monkeypatch):
    p, proxy = _publisher(monkeypatch, good={'a', 'b'})

    container, rejected = p._create_carousel_container('ig-1', 'cap', ['c1'], ['a', 'b'])

    assert rejected == []
    # One call, not one per handle: probing is only for the failure path.
    assert proxy.calls == [['a', 'b']]
