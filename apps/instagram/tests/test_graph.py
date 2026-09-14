import httpx
import pytest
from django.test import override_settings

from apps.instagram.services.graph import GraphAPIError, InstagramGraph


def _graph(handler) -> InstagramGraph:
    return InstagramGraph(
        "IGQVJtoken", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


@override_settings(META_MOCK=False)
def test_get_sends_the_token_to_the_versioned_path():
    def handler(request):
        assert request.url.path == "/v21.0/me"
        assert request.url.params["access_token"] == "IGQVJtoken"
        assert request.url.params["fields"] == "id"
        return httpx.Response(200, json={"id": "1"})

    assert _graph(handler).get("me", {"fields": "id"}) == {"id": "1"}


@override_settings(META_MOCK=False)
@pytest.mark.parametrize(
    ("code", "token_error", "rate_limited", "permission_error"),
    [(190, True, False, False), (4, False, True, False), (10, False, False, True),
     (200, False, False, True)],
)
def test_api_errors_are_classified(code, token_error, rate_limited, permission_error):
    graph = _graph(
        lambda request: httpx.Response(400, json={"error": {"message": "nope", "code": code}})
    )
    with pytest.raises(GraphAPIError) as caught:
        graph.get("me")
    error = caught.value
    assert (error.is_token_error, error.is_rate_limited, error.is_permission_error) == (
        token_error, rate_limited, permission_error,
    )


@override_settings(META_MOCK=False)
def test_network_failures_are_transient():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(GraphAPIError) as caught:
        _graph(handler).get("me")
    assert caught.value.is_transient


@override_settings(META_MOCK=False)
def test_paginate_follows_next_links_up_to_the_page_limit():
    calls = []

    def handler(request):
        calls.append(request.url)
        n = len(calls)
        return httpx.Response(200, json={
            "data": [{"id": str(n)}],
            "paging": {"next": f"https://graph.instagram.com/v21.0/me/media?after={n}"},
        })

    items = list(_graph(handler).paginate("me/media", max_pages=3))
    assert [item["id"] for item in items] == ["1", "2", "3"]
    assert len(calls) == 3


@override_settings(META_MOCK=False)
def test_paginate_refuses_links_outside_the_graph_api():
    def handler(request):
        return httpx.Response(200, json={"data": [], "paging": {"next": "https://evil.example/x"}})

    with pytest.raises(GraphAPIError):
        list(_graph(handler).paginate("me/media"))
