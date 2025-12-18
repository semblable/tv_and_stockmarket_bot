import pytest
from unittest.mock import patch, MagicMock
from utils import chart_utils, paginator
from utils.article_utils import extract_readable_text_from_html

# --- Chart Utils Tests ---

@patch('utils.chart_utils.requests.post')
def test_generate_stock_chart_url_success(mock_post):
    # Mock successful short URL response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True, "url": "https://quickchart.io/chart/render/short-url"}
    mock_post.return_value = mock_response

    data_points = [("2023-01-01 10:00:00", 100.0), ("2023-01-02 10:00:00", 105.0)]
    url = chart_utils.generate_stock_chart_url("TEST", "1D", data_points)
    
    assert url == "https://quickchart.io/chart/render/short-url"
    mock_post.assert_called_once()

@patch('utils.chart_utils.requests.post')
def test_generate_stock_chart_url_fallback(mock_post):
    # Mock failure of short URL, should fall back to long URL
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_post.return_value = mock_response

    data_points = [("2023-01-01", 100.0), ("2023-01-02", 105.0)]
    url = chart_utils.generate_stock_chart_url("TEST", "1D", data_points)
    
    assert url is not None
    assert "https://quickchart.io/chart" in url
    assert "c=" in url # Should contain config param

def test_generate_stock_chart_url_empty_data():
    url = chart_utils.generate_stock_chart_url("TEST", "1D", [])
    assert url is None

# --- Paginator Tests ---

@pytest.mark.asyncio
async def test_paginator_init_logic():
    items = list(range(1, 21)) # 20 items
    user_id = 123
    
    # Case 1: 5 items per page (default) -> 4 pages
    view = paginator.BasePaginatorView(user_id=user_id, items=items)
    assert view.total_pages == 4
    assert view.current_page == 0
    
    # Case 2: 10 items per page -> 2 pages
    view = paginator.BasePaginatorView(user_id=user_id, items=items, items_per_page=10)
    assert view.total_pages == 2

    # Case 3: 6 items per page -> 4 pages (20/6 = 3.33 -> 4)
    view = paginator.BasePaginatorView(user_id=user_id, items=items, items_per_page=6)
    assert view.total_pages == 4

    # Case 4: Empty items
    view = paginator.BasePaginatorView(user_id=user_id, items=[])
    assert view.total_pages == 0

@pytest.mark.asyncio
async def test_paginator_button_states():
    # We can test internal state update logic without full Discord interaction
    items = list(range(1, 11)) # 10 items, 2 pages (default 5 per page)
    user_id = 123
    view = paginator.BasePaginatorView(user_id=user_id, items=items)
    
    # Mock buttons (since they are created by decorators, we need to check if attributes exist or mock them if needed)
    # In unit tests without Discord context, the button attributes like 'first_page_button' exist as Unbound methods or descriptors usually.
    # However, inspecting the instance, we can manually check logic if we extract it. 
    # But _update_button_states modifies .disabled on buttons. 
    # We need to mock the buttons on the view instance.
    
    view.first_page_button = MagicMock()
    view.prev_page_button = MagicMock()
    view.next_page_button = MagicMock()
    view.last_page_button = MagicMock()
    
    # Page 0 (Start)
    view.current_page = 0
    view._update_button_states()
    
    assert view.first_page_button.disabled is True
    assert view.prev_page_button.disabled is True
    assert view.next_page_button.disabled is False
    assert view.last_page_button.disabled is False
    
    # Page 1 (End)
    view.current_page = 1
    view._update_button_states()
    
    assert view.first_page_button.disabled is False
    assert view.prev_page_button.disabled is False
    assert view.next_page_button.disabled is True
    assert view.last_page_button.disabled is True


def test_article_html_extraction_basic():
    html = """
    <html><head><title>x</title><script>var a=1;</script></head>
    <body>
      <nav>menu</nav>
      <article>
        <h1>Big headline</h1>
        <p>This is the first paragraph of the article. It has enough length to be included.</p>
        <p>Second paragraph with more details about the story. It should also be included.</p>
      </article>
      <footer>copyright</footer>
    </body></html>
    """
    text = extract_readable_text_from_html(html)
    assert "Big headline" in text
    assert "first paragraph" in text
    assert "Second paragraph" in text
    assert "menu" not in text


