"""
Permission Checker Tests
Kiểm tra tính năng kiểm tra quyền truy cập
"""

import pytest
from backend.utils.permission_checker import (
    FacebookPermissionChecker, 
    SourceAccessValidator,
    PermissionStatus
)


class TestFacebookPermissionChecker:
    """Kiểm tra Facebook Permission Checker"""
    
    def test_check_access_valid_group(self):
        """Kiểm tra quyền truy cập group hợp lệ"""
        result = FacebookPermissionChecker.check_access(
            facebook_id="123456789",
            user_id=1,
            source_type="group"
        )
        
        assert result['facebook_id'] == "123456789"
        assert result['accessible'] is not None
        assert result['status'] in [
            PermissionStatus.GRANTED,
            PermissionStatus.RESTRICTED,
            PermissionStatus.DENIED,
            PermissionStatus.ERROR
        ]
    
    def test_check_access_page(self):
        """Kiểm tra quyền truy cập page"""
        result = FacebookPermissionChecker.check_access(
            facebook_id="mypage",
            user_id=1,
            source_type="page"
        )
        
        assert result['facebook_id'] == "mypage"
        assert result['status'] is not None
    
    def test_check_access_invalid_id(self):
        """Kiểm tra ID không hợp lệ"""
        result = FacebookPermissionChecker.check_access(
            facebook_id="",
            user_id=1,
            source_type="group"
        )
        
        assert result['status'] == PermissionStatus.ERROR
        assert result['accessible'] == False
    
    def test_cache_functionality(self):
        """Kiểm tra tính năng cache"""
        facebook_id = "123456789"
        user_id = 1
        
        # Lần đầu tiên - không có cache
        result1 = FacebookPermissionChecker.check_access(
            facebook_id=facebook_id,
            user_id=user_id,
            source_type="group"
        )
        
        # Lần thứ hai - có cache
        result2 = FacebookPermissionChecker.check_access(
            facebook_id=facebook_id,
            user_id=user_id,
            source_type="group"
        )
        
        # Kết quả từ cache phải có trường 'cached'
        assert result2.get('cached') == True
    
    def test_clear_cache(self):
        """Kiểm tra xóa cache"""
        FacebookPermissionChecker.check_access(
            facebook_id="123456789",
            user_id=1,
            source_type="group"
        )
        
        stats_before = FacebookPermissionChecker.get_cache_stats()
        assert stats_before['cached_items'] > 0
        
        FacebookPermissionChecker.clear_cache()
        
        stats_after = FacebookPermissionChecker.get_cache_stats()
        assert stats_after['cached_items'] == 0
    
    def test_check_bulk_access(self):
        """Kiểm tra kiểm tra quyền hàng loạt"""
        sources = [
            {'facebook_id': '123456789', 'source_type': 'group'},
            {'facebook_id': 'mypage', 'source_type': 'page'},
            {'facebook_id': 'username', 'source_type': 'user'},
        ]
        
        results = FacebookPermissionChecker.check_bulk_access(sources, user_id=1)
        
        assert len(results) == 3
        assert '123456789' in results
        assert 'mypage' in results
        assert 'username' in results


class TestSourceAccessValidator:
    """Kiểm tra Source Access Validator"""
    
    def test_validate_before_save_valid(self):
        """Kiểm tra xác thực trước khi lưu - hợp lệ"""
        is_valid, error = SourceAccessValidator.validate_before_save(
            facebook_id="123456789",
            source_type="group",
            user_id=1
        )
        
        # Có thể hợp lệ hoặc không, tùy vào kết quả kiểm tra quyền
        assert isinstance(is_valid, bool)
    
    def test_validate_before_save_invalid_id(self):
        """Kiểm tra xác thực - ID không hợp lệ"""
        is_valid, error = SourceAccessValidator.validate_before_save(
            facebook_id="",
            source_type="group",
            user_id=1
        )
        
        assert is_valid == False
        assert error is not None
    
    def test_validate_before_save_invalid_type(self):
        """Kiểm tra xác thực - loại không hợp lệ"""
        is_valid, error = SourceAccessValidator.validate_before_save(
            facebook_id="123456789",
            source_type="invalid_type",
            user_id=1
        )
        
        assert is_valid == False
        assert error is not None
    
    def test_validate_before_save_strict_mode(self):
        """Kiểm tra xác thực chế độ strict"""
        # Strict mode: chỉ chấp nhận GRANTED status
        is_valid, error = SourceAccessValidator.validate_before_save(
            facebook_id="123456789",
            source_type="group",
            user_id=1,
            strict_mode=True
        )
        
        # Có thể là False (nếu status không phải GRANTED)
        assert isinstance(is_valid, bool)
    
    def test_get_validation_warning(self):
        """Kiểm tra lấy cảnh báo xác thực"""
        result = FacebookPermissionChecker.check_access(
            facebook_id="123456789",
            user_id=1,
            source_type="group"
        )
        
        warning = SourceAccessValidator.get_validation_warning(result)
        
        # Warning có thể là None hoặc str
        assert warning is None or isinstance(warning, str)


class TestPermissionStatusEnum:
    """Kiểm tra PermissionStatus Enum"""
    
    def test_permission_status_values(self):
        """Kiểm tra các giá trị của PermissionStatus"""
        assert PermissionStatus.GRANTED.value == "granted"
        assert PermissionStatus.DENIED.value == "denied"
        assert PermissionStatus.RESTRICTED.value == "restricted"
        assert PermissionStatus.NOT_CHECKED.value == "not_checked"
        assert PermissionStatus.ERROR.value == "error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
