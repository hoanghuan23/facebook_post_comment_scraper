"""
Facebook URL Parser
Hỗ trợ phân tích và trích xuất thông tin từ URL Facebook
"""

import re
from typing import Optional, Dict, Tuple
from enum import Enum
from urllib.parse import urlparse, parse_qs


class FacebookSourceType(str, Enum):
    """Loại nguồn Facebook"""
    GROUP = "group"
    PAGE = "page"
    USER = "user"
    POST = "post"
    UNKNOWN = "unknown"


class FacebookURLParser:
    """Parser cho URL Facebook"""
    
    # Regex patterns cho các loại URL
    PATTERNS = {
        # Format: facebook.com/groups/123456 or facebook.com/groups/group-name
        'group_numeric': r'(?:https?://)?(?:www\.)?facebook\.com/groups/(\d+)',
        'group_slug': r'(?:https?://)?(?:www\.)?facebook\.com/groups/([a-zA-Z0-9\-_.]+)',
        
        # Format: facebook.com/page-name or facebook.com/pages/Page-Name/123456
        'page_numeric': r'(?:https?://)?(?:www\.)?facebook\.com/pages/[^/]+/(\d+)',
        'page_slug': r'(?:https?://)?(?:www\.)?facebook\.com/([a-zA-Z0-9\-_.]+)/?$',
        
        # Format: facebook.com/username or facebook.com/profile.php?id=123456
        'user_profile_id': r'(?:https?://)?(?:www\.)?facebook\.com/profile\.php\?id=(\d+)',
        'user_slug': r'(?:https?://)?(?:www\.)?facebook\.com/([a-zA-Z0-9\-_.]+)/?$',
        
        # Format: facebook.com/photo.php?fbid=123456 or /posts/123456
        'post_id': r'(?:https?://)?(?:www\.)?facebook\.com/(?:photo|post)\.php\?.*(?:fbid|id)=(\d+)',
        'post_numeric': r'(?:https?://)?(?:www\.)?facebook\.com/[^/]+/(?:posts|photos|videos)/(\d+)',
    }
    
    @staticmethod
    def _clean_url(url: str) -> str:
        """Làm sạch URL"""
        return url.strip().rstrip('/').replace('?__cft__[0]=', '?').split('&')[0]
    
    @staticmethod
    def _extract_by_patterns(url: str, patterns: Dict[str, str]) -> Optional[Tuple[str, str]]:
        """Trích xuất ID bằng các pattern"""
        for pattern_name, pattern in patterns.items():
            match = re.match(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1), pattern_name
        return None
    
    @classmethod
    def parse(cls, url: str) -> Dict:
        """
        Phân tích URL Facebook
        
        Args:
            url: URL cần phân tích
            
        Returns:
            Dict chứa:
            - facebook_id: ID hoặc slug của nguồn
            - source_type: Loại nguồn (group/page/user/post)
            - original_url: URL gốc
            - is_valid: Có hợp lệ hay không
            - error: Lỗi nếu có
        """
        result = {
            'facebook_id': None,
            'source_type': FacebookSourceType.UNKNOWN,
            'original_url': url,
            'is_valid': False,
            'error': None,
            'pattern_matched': None,
        }
        
        if not url or not isinstance(url, str):
            result['error'] = 'URL không hợp lệ'
            return result
        
        url = cls._clean_url(url)
        
        # Kiểm tra URL hợp lệ
        if not cls._is_valid_facebook_url(url):
            result['error'] = 'URL không phải từ Facebook'
            return result
        
        # Thử phát hiện group
        group_result = cls._extract_by_patterns(url, {
            'group_numeric': cls.PATTERNS['group_numeric'],
            'group_slug': cls.PATTERNS['group_slug'],
        })
        if group_result:
            result['facebook_id'] = group_result[0]
            result['source_type'] = FacebookSourceType.GROUP
            result['pattern_matched'] = group_result[1]
            result['is_valid'] = True
            return result
        
        # Thử phát hiện page
        page_numeric = re.match(cls.PATTERNS['page_numeric'], url, re.IGNORECASE)
        if page_numeric:
            result['facebook_id'] = page_numeric.group(1)
            result['source_type'] = FacebookSourceType.PAGE
            result['pattern_matched'] = 'page_numeric'
            result['is_valid'] = True
            return result
        
        # Thử phát hiện user profile ID
        user_id = re.match(cls.PATTERNS['user_profile_id'], url, re.IGNORECASE)
        if user_id:
            result['facebook_id'] = user_id.group(1)
            result['source_type'] = FacebookSourceType.USER
            result['pattern_matched'] = 'user_profile_id'
            result['is_valid'] = True
            return result
        
        # Thử phát hiện post
        post_result = cls._extract_by_patterns(url, {
            'post_id': cls.PATTERNS['post_id'],
            'post_numeric': cls.PATTERNS['post_numeric'],
        })
        if post_result:
            result['facebook_id'] = post_result[0]
            result['source_type'] = FacebookSourceType.POST
            result['pattern_matched'] = post_result[1]
            result['is_valid'] = True
            return result
        
        # Fallback: slug
        slug_match = re.match(cls.PATTERNS['user_slug'], url, re.IGNORECASE)
        if slug_match:
            slug = slug_match.group(1)
            # Tránh các từ khóa đặc biệt
            if slug not in ['groups', 'pages', 'photo', 'post', 'watch', 'video', 'marketplace']:
                result['facebook_id'] = slug
                result['source_type'] = FacebookSourceType.PAGE
                result['pattern_matched'] = 'user_slug'
                result['is_valid'] = True
                return result
        
        result['error'] = 'Không thể trích xuất ID từ URL'
        return result
    
    @staticmethod
    def _is_valid_facebook_url(url: str) -> bool:
        """Kiểm tra URL có phải từ Facebook không"""
        return bool(re.search(r'facebook\.com', url, re.IGNORECASE))
    
    @classmethod
    def extract_facebook_id(cls, url: str) -> Optional[str]:
        """
        Trích xuất Facebook ID/slug từ URL
        
        Returns:
            Facebook ID nếu hợp lệ, None nếu không
        """
        result = cls.parse(url)
        return result['facebook_id'] if result['is_valid'] else None
    
    @classmethod
    def detect_source_type(cls, url: str) -> FacebookSourceType:
        """
        Phát hiện loại nguồn từ URL
        
        Returns:
            FacebookSourceType
        """
        result = cls.parse(url)
        return result['source_type']
    
    @classmethod
    def validate_url(cls, url: str) -> Tuple[bool, Optional[str]]:
        """
        Kiểm tra xem URL có hợp lệ không
        
        Returns:
            (is_valid, error_message)
        """
        result = cls.parse(url)
        return (result['is_valid'], result['error'])


# Ví dụ URL được hỗ trợ:
# Groups:
#   - https://www.facebook.com/groups/123456
#   - https://www.facebook.com/groups/my-group-name
#
# Pages:
#   - https://www.facebook.com/mypage
#   - https://www.facebook.com/pages/Page-Name/123456
#
# Users:
#   - https://www.facebook.com/username
#   - https://www.facebook.com/profile.php?id=123456
#
# Posts:
#   - https://www.facebook.com/photo.php?fbid=123456
#   - https://www.facebook.com/user/posts/123456
