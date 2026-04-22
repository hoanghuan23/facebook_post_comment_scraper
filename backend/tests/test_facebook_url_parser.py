"""
Facebook URL Parser Tests
Kiểm tra tính năng phân tích URL Facebook
"""

import pytest
from backend.utils.facebook_url_parser import FacebookURLParser, FacebookSourceType


class TestFacebookURLParser:
    """Kiểm tra Facebook URL Parser"""
    
    def test_parse_group_numeric(self):
        """Kiểm tra phân tích group URL với ID số"""
        url = "https://www.facebook.com/groups/123456789"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == True
        assert result['facebook_id'] == "123456789"
        assert result['source_type'] == FacebookSourceType.GROUP
    
    def test_parse_group_slug(self):
        """Kiểm tra phân tích group URL với slug"""
        url = "https://www.facebook.com/groups/my-group-name"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == True
        assert result['facebook_id'] == "my-group-name"
        assert result['source_type'] == FacebookSourceType.GROUP
    
    def test_parse_page(self):
        """Kiểm tra phân tích page URL"""
        url = "https://www.facebook.com/mypage"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == True
        assert result['facebook_id'] == "mypage"
        assert result['source_type'] == FacebookSourceType.PAGE
    
    def test_parse_page_numeric(self):
        """Kiểm tra phân tích page URL với ID số"""
        url = "https://www.facebook.com/pages/Page-Name/987654321"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == True
        assert result['facebook_id'] == "987654321"
        assert result['source_type'] == FacebookSourceType.PAGE
    
    def test_parse_user_profile_id(self):
        """Kiểm tra phân tích user profile với ID"""
        url = "https://www.facebook.com/profile.php?id=123456789"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == True
        assert result['facebook_id'] == "123456789"
        assert result['source_type'] == FacebookSourceType.USER
    
    def test_parse_user_slug(self):
        """Kiểm tra phân tích user URL với slug"""
        url = "https://www.facebook.com/username"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == True
        assert result['facebook_id'] == "username"
        assert result['source_type'] == FacebookSourceType.PAGE  # Could be page or user
    
    def test_parse_post_id(self):
        """Kiểm tra phân tích post URL"""
        url = "https://www.facebook.com/photo.php?fbid=123456789"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == True
        assert result['facebook_id'] == "123456789"
        assert result['source_type'] == FacebookSourceType.POST
    
    def test_invalid_url(self):
        """Kiểm tra URL không hợp lệ"""
        url = "https://www.google.com"
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == False
        assert result['error'] is not None
    
    def test_empty_url(self):
        """Kiểm tra URL trống"""
        url = ""
        result = FacebookURLParser.parse(url)
        
        assert result['is_valid'] == False
    
    def test_extract_facebook_id(self):
        """Kiểm tra trích xuất Facebook ID"""
        url = "https://www.facebook.com/groups/123456789"
        facebook_id = FacebookURLParser.extract_facebook_id(url)
        
        assert facebook_id == "123456789"
    
    def test_validate_url(self):
        """Kiểm tra xác thực URL"""
        valid_url = "https://www.facebook.com/groups/123456"
        is_valid, error = FacebookURLParser.validate_url(valid_url)
        
        assert is_valid == True
        assert error is None
        
        invalid_url = "https://www.google.com"
        is_valid, error = FacebookURLParser.validate_url(invalid_url)
        
        assert is_valid == False
        assert error is not None
    
    def test_detect_source_type(self):
        """Kiểm tra phát hiện loại source"""
        group_url = "https://www.facebook.com/groups/123456"
        assert FacebookURLParser.detect_source_type(group_url) == FacebookSourceType.GROUP
        
        page_url = "https://www.facebook.com/mypage"
        assert FacebookURLParser.detect_source_type(page_url) == FacebookSourceType.PAGE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
