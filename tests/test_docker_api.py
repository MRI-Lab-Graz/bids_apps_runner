import requests


def fetch_docker_tags(repo: str):
    url = (
        f"https://registry.hub.docker.com/v2/repositories/{repo}/tags"
        "?page_size=100&ordering=last_updated"
    )
    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        return []

    data = response.json()
    return [tag["name"] for tag in data.get("results", [])]


def test_fetch_docker_tags_success(monkeypatch):
    class DummyResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [{"name": "24.1.0"}, {"name": "24.0.0"}]}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: DummyResponse())

    assert fetch_docker_tags("nipreps/fmriprep") == ["24.1.0", "24.0.0"]


def test_fetch_docker_tags_non_200(monkeypatch):
    class DummyResponse:
        status_code = 503

        @staticmethod
        def json():
            return {"results": []}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: DummyResponse())

    assert fetch_docker_tags("nipreps/fmriprep") == []


def test_fetch_docker_tags_request_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "get", _raise)

    try:
        fetch_docker_tags("nipreps/fmriprep")
    except requests.RequestException:
        pass
    else:
        raise AssertionError("Expected RequestException to be raised")

