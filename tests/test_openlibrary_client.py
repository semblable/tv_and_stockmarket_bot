# tests/test_openlibrary_client.py
from unittest.mock import MagicMock, patch

from api_clients import openlibrary_client


@patch("api_clients.openlibrary_client.requests.get")
def test_search_authors_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "docs": [
            {"key": "/authors/OL23919A", "name": "Agatha Christie", "top_work": "Murder on the Orient Express", "work_count": 300},
            {"key": "/authors/OLX", "name": "Bad Key"},  # invalid id, should be filtered
        ]
    }
    mock_get.return_value = mock_response

    results = openlibrary_client.search_authors("Agatha", limit=5)
    assert len(results) == 1
    assert results[0]["author_id"] == "OL23919A"
    assert results[0]["name"] == "Agatha Christie"


@patch("api_clients.openlibrary_client.requests.get")
def test_get_author_works_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "entries": [
            {"key": "/works/OL82563W", "title": "Test Book", "first_publish_date": "2026"},
            {"key": "/works/OL1X", "title": "Bad Work"},  # invalid id
        ]
    }
    mock_get.return_value = mock_response

    works = openlibrary_client.get_author_works("OL23919A", limit=10)
    assert len(works) == 1
    assert works[0]["work_id"] == "OL82563W"
    assert works[0]["title"] == "Test Book"


@patch("api_clients.openlibrary_client.requests.get")
def test_search_books_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "docs": [
            {
                "key": "/works/OL82563W",
                "title": "Dune",
                "author_name": ["Frank Herbert"],
                "first_publish_year": 1965,
                "cover_i": 123,
                "edition_key": ["OL1M"],
                "isbn": ["9780441013593"],
                "number_of_pages_median": 412,
            },
            {"key": "/works/OL1X", "title": "Bad Work"},  # invalid id
        ]
    }
    mock_get.return_value = mock_response

    results = openlibrary_client.search_books("dune", limit=5)
    assert len(results) == 1
    r = results[0]
    assert r["work_id"] == "OL82563W"
    assert r["title"] == "Dune"
    assert r["author"] == "Frank Herbert"
    assert r["first_publish_year"] == 1965
    assert r["cover_id"] == 123
    assert isinstance(r["cover_url"], str) and "covers.openlibrary.org" in r["cover_url"]
    assert r["edition_id"] == "OL1M"
    assert r["isbn"] == "9780441013593"
    assert r["pages_median"] == 412


