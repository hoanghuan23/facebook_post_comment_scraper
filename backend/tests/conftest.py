"""
Pytest Configuration and Fixtures
"""

import pytest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def sample_facebook_urls():
    """Sample Facebook URLs for testing"""
    return {
        'group_numeric': 'https://www.facebook.com/groups/123456789',
        'group_slug': 'https://www.facebook.com/groups/my-group-name',
        'page': 'https://www.facebook.com/mypage',
        'page_numeric': 'https://www.facebook.com/pages/Page-Name/987654321',
        'user_profile': 'https://www.facebook.com/profile.php?id=123456789',
        'user_slug': 'https://www.facebook.com/username',
        'post': 'https://www.facebook.com/photo.php?fbid=123456789',
    }


@pytest.fixture
def sample_facebook_ids():
    """Sample Facebook IDs for testing"""
    return {
        'group_numeric': '123456789',
        'group_slug': 'my-group-name',
        'page': 'mypage',
        'user_slug': 'username',
    }


@pytest.fixture
def sample_source_data():
    """Sample source data for testing"""
    return {
        'group': {
            'facebook_id': '123456789',
            'source_type': 'group',
            'facebook_url': 'https://www.facebook.com/groups/123456789',
        },
        'page': {
            'facebook_id': 'mypage',
            'source_type': 'page',
            'facebook_url': 'https://www.facebook.com/mypage',
        },
        'user': {
            'facebook_id': '123456789',
            'source_type': 'user',
            'facebook_url': 'https://www.facebook.com/profile.php?id=123456789',
        },
    }
