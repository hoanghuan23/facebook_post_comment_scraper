"""
Facebook Permission Checker
Kiểm tra quyền truy cập và xác thực trước khi lưu source
"""

from typing import Optional, Dict, Tuple
from enum import Enum
import requests
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PermissionStatus(str, Enum):
    """Trạng thái quyền truy cập"""
    GRANTED = "granted"
    DENIED = "denied"
    RESTRICTED = "restricted"
    NOT_CHECKED = "not_checked"
    ERROR = "error"


class FacebookPermissionChecker:
    """Kiểm tra quyền truy cập Facebook"""
    
    # Cache kết quả kiểm tra (lưu trong 1 giờ)
    _cache: Dict[str, Dict] = {}
    _cache_ttl = timedelta(hours=1)
    
    @staticmethod
    def _get_cache_key(facebook_id: str, user_id: int) -> str:
        """Tạo cache key"""
        return f"{user_id}:{facebook_id}"
    
    @classmethod
    def _get_cached_result(cls, facebook_id: str, user_id: int) -> Optional[Dict]:
        """Lấy kết quả từ cache nếu còn hạn"""
        key = cls._get_cache_key(facebook_id, user_id)
        if key in cls._cache:
            cached = cls._cache[key]
            if datetime.utcnow() - cached.get('timestamp', datetime.utcnow()) < cls._cache_ttl:
                return cached['result']
        return None
    
    @classmethod
    def _set_cache(cls, facebook_id: str, user_id: int, result: Dict):
        """Lưu kết quả vào cache"""
        key = cls._get_cache_key(facebook_id, user_id)
        cls._cache[key] = {
            'result': result,
            'timestamp': datetime.utcnow()
        }
    
    @classmethod
    def check_access(cls, facebook_id: str, user_id: int, 
                     source_type: str, user_cookies: Optional[Dict] = None) -> Dict:
        """
        Kiểm tra quyền truy cập tới source trên Facebook
        
        Args:
            facebook_id: ID hoặc slug của group/page/user
            user_id: ID của user trong hệ thống
            source_type: Loại nguồn (group/page/user)
            user_cookies: Cookie Facebook của user
            
        Returns:
            Dict chứa:
            - status: PermissionStatus
            - message: Thông báo chi tiết
            - accessible: Boolean (có truy cập được không)
            - restrictions: Các hạn chế nếu có
            - checked_at: Thời gian kiểm tra
        """
        
        result = {
            'status': PermissionStatus.NOT_CHECKED,
            'message': 'Chưa kiểm tra',
            'accessible': False,
            'restrictions': [],
            'checked_at': datetime.utcnow().isoformat(),
            'facebook_id': facebook_id,
        }
        
        # Kiểm tra cache
        cached = cls._get_cached_result(facebook_id, user_id)
        if cached:
            result.update(cached)
            result['cached'] = True
            return result
        
        # Xác thực cơ bản
        if not cls._validate_basic(facebook_id, source_type):
            result['status'] = PermissionStatus.ERROR
            result['message'] = 'Facebook ID không hợp lệ'
            result['accessible'] = False
            cls._set_cache(facebook_id, user_id, result)
            return result
        
        # Kiểm tra quyền dựa trên source type
        if not user_cookies:
            # Nếu không có cookie, giả định có thể truy cập được
            # (thường là public groups/pages)
            result.update(cls._check_public_access(facebook_id, source_type))
        else:
            result.update(cls._check_authenticated_access(facebook_id, source_type, user_cookies))
        
        # Lưu vào cache
        cls._set_cache(facebook_id, user_id, result)
        return result
    
    @staticmethod
    def _validate_basic(facebook_id: str, source_type: str) -> bool:
        """Xác thực cơ bản Facebook ID"""
        if not facebook_id or not isinstance(facebook_id, str):
            return False
        
        # ID số phải là số
        if facebook_id.isdigit() and len(facebook_id) < 10:
            return False
        
        return True
    
    @staticmethod
    def _check_public_access(facebook_id: str, source_type: str) -> Dict:
        """Kiểm tra truy cập công khai"""
        result = {
            'status': PermissionStatus.GRANTED,
            'message': 'Có thể truy cập (công khai)',
            'accessible': True,
            'restrictions': [],
        }
        
        # Group thường cần quyền (private hoặc public)
        if source_type == 'group':
            result['status'] = PermissionStatus.RESTRICTED
            result['message'] = 'Group có thể yêu cầu xác thực'
            result['restrictions'] = ['may_require_auth']
        
        # Page thường công khai
        elif source_type == 'page':
            result['message'] = 'Page công khai, có thể truy cập'
        
        # User có thể bị hạn chế
        elif source_type == 'user':
            result['status'] = PermissionStatus.RESTRICTED
            result['message'] = 'Hồ sơ user có thể được giới hạn'
            result['restrictions'] = ['privacy_settings']
        
        return result
    
    @staticmethod
    def _check_authenticated_access(facebook_id: str, source_type: str, 
                                    user_cookies: Dict) -> Dict:
        """Kiểm tra truy cập với xác thực"""
        result = {
            'status': PermissionStatus.GRANTED,
            'message': 'Có quyền truy cập',
            'accessible': True,
            'restrictions': [],
        }
        
        # Giả lập kiểm tra (trong thực tế sẽ gọi API Facebook)
        try:
            # Kiểm tra xem có cookie hợp lệ không
            if not user_cookies or 'c_user' not in str(user_cookies):
                result['status'] = PermissionStatus.DENIED
                result['message'] = 'Cookie không hợp lệ hoặc hết hạn'
                result['accessible'] = False
                return result
            
            # Group cần kiểm tra membership
            if source_type == 'group':
                result['message'] = 'Có quyền truy cập group'
                result['restrictions'] = ['group_member_required']
            
            # User cần kiểm tra kết bạn/public
            elif source_type == 'user':
                result['message'] = 'Có quyền truy cập hồ sơ user'
                result['restrictions'] = ['depends_on_privacy']
            
            # Page thường không cần quyền đặc biệt
            else:
                result['message'] = 'Có quyền truy cập page'
            
        except Exception as e:
            logger.error(f"Error checking authenticated access: {e}")
            result['status'] = PermissionStatus.ERROR
            result['message'] = 'Lỗi khi kiểm tra quyền'
            result['accessible'] = False
        
        return result
    
    @classmethod
    def check_bulk_access(cls, sources: list, user_id: int, 
                         user_cookies: Optional[Dict] = None) -> Dict[str, Dict]:
        """
        Kiểm tra quyền truy cập hàng loạt
        
        Args:
            sources: List các dict với keys: facebook_id, source_type
            user_id: ID của user
            user_cookies: Cookie Facebook
            
        Returns:
            Dict mapping facebook_id -> access result
        """
        results = {}
        for source in sources:
            facebook_id = source.get('facebook_id')
            source_type = source.get('source_type')
            
            if facebook_id and source_type:
                results[facebook_id] = cls.check_access(
                    facebook_id, user_id, source_type, user_cookies
                )
        
        return results
    
    @classmethod
    def clear_cache(cls):
        """Xóa cache"""
        cls._cache.clear()
    
    @classmethod
    def get_cache_stats(cls) -> Dict:
        """Lấy thống kê cache"""
        return {
            'cached_items': len(cls._cache),
            'cache_ttl_minutes': cls._cache_ttl.total_seconds() / 60,
        }


class SourceAccessValidator:
    """Xác thực truy cập source trước khi lưu"""
    
    @staticmethod
    def validate_before_save(facebook_id: str, source_type: str, user_id: int,
                            user_cookies: Optional[Dict] = None, 
                            strict_mode: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Xác thực trước khi lưu source
        
        Args:
            facebook_id: ID nguồn
            source_type: Loại nguồn
            user_id: ID user
            user_cookies: Cookie Facebook
            strict_mode: Nếu True, chỉ cho phép truy cập được grant
            
        Returns:
            (is_valid, error_message)
        """
        
        # Kiểm tra ID hợp lệ
        if not facebook_id or not source_type:
            return False, 'Facebook ID hoặc loại nguồn bị thiếu'
        
        # Kiểm tra loại nguồn hợp lệ
        valid_types = ['group', 'page', 'user', 'post']
        if source_type not in valid_types:
            return False, f'Loại nguồn không hợp lệ: {source_type}'
        
        # Kiểm tra quyền truy cập
        permission_result = FacebookPermissionChecker.check_access(
            facebook_id, user_id, source_type, user_cookies
        )
        
        # Nếu bị lỗi
        if permission_result['status'] == PermissionStatus.ERROR:
            return False, permission_result['message']
        
        # Nếu bị từ chối
        if permission_result['status'] == PermissionStatus.DENIED:
            return False, 'Không có quyền truy cập nguồn này'
        
        # Nếu strict mode, chỉ cho phép GRANTED
        if strict_mode and permission_result['status'] != PermissionStatus.GRANTED:
            return False, f"Quyền truy cập bị hạn chế: {', '.join(permission_result['restrictions'])}"
        
        # Có thể truy cập được
        return True, None
    
    @staticmethod
    def get_validation_warning(permission_result: Dict) -> Optional[str]:
        """Lấy cảnh báo nếu có từ kết quả kiểm tra quyền"""
        if permission_result['status'] == PermissionStatus.RESTRICTED:
            restrictions = permission_result.get('restrictions', [])
            messages = {
                'may_require_auth': 'Group này có thể yêu cầu xác thực',
                'privacy_settings': 'Hồ sơ user được bảo vệ bởi cài đặt riêng tư',
                'group_member_required': 'Cần là thành viên của group',
                'depends_on_privacy': 'Phụ thuộc vào cài đặt riêng tư của user',
            }
            
            warning_parts = []
            for restriction in restrictions:
                if restriction in messages:
                    warning_parts.append(messages[restriction])
            
            return ' | '.join(warning_parts) if warning_parts else None
        
        return None
