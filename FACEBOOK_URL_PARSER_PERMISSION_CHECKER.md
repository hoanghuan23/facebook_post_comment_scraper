# Facebook URL Parser & Permission Checker

## Tổng Quan

Hai module mới được thêm vào dự án để cải thiện xử lý URL Facebook và kiểm tra quyền truy cập:

1. **Facebook URL Parser** (`backend/utils/facebook_url_parser.py`)
   - Phân tích và trích xuất thông tin từ URL Facebook
   - Hỗ trợ Group, Page, User, Post
   - Xác thực URL

2. **Permission Checker** (`backend/utils/permission_checker.py`)
   - Kiểm tra quyền truy cập tới Facebook source
   - Xác thực trước khi lưu source
   - Cache kết quả kiểm tra

---

## 1. Facebook URL Parser

### Chức Năng

Phân tích các định dạng URL Facebook khác nhau:

#### Group
- `https://www.facebook.com/groups/123456789` (ID số)
- `https://www.facebook.com/groups/my-group-name` (slug)

#### Page
- `https://www.facebook.com/mypage`
- `https://www.facebook.com/pages/Page-Name/123456789`

#### User
- `https://www.facebook.com/username`
- `https://www.facebook.com/profile.php?id=123456789`

#### Post
- `https://www.facebook.com/photo.php?fbid=123456789`
- `https://www.facebook.com/username/posts/123456789`

### API

```python
from backend.utils.facebook_url_parser import FacebookURLParser

# Phân tích URL đầy đủ
result = FacebookURLParser.parse("https://www.facebook.com/groups/123456")
# Returns: {
#     'facebook_id': '123456',
#     'source_type': FacebookSourceType.GROUP,
#     'is_valid': True,
#     'original_url': '...',
#     'error': None
# }

# Trích xuất Facebook ID
facebook_id = FacebookURLParser.extract_facebook_id("https://www.facebook.com/groups/123456")
# Returns: '123456'

# Phát hiện loại source
source_type = FacebookURLParser.detect_source_type("https://www.facebook.com/groups/123456")
# Returns: FacebookSourceType.GROUP

# Xác thực URL
is_valid, error = FacebookURLParser.validate_url("https://www.facebook.com/groups/123456")
# Returns: (True, None)
```

---

## 2. Permission Checker

### Chức Năng

Kiểm tra quyền truy cập tới Facebook source trước khi lưu.

### Trạng Thái Quyền

```python
from backend.utils.permission_checker import PermissionStatus

- GRANTED: Có quyền truy cập đầy đủ
- DENIED: Bị từ chối quyền truy cập
- RESTRICTED: Có hạn chế nào đó
- NOT_CHECKED: Chưa kiểm tra
- ERROR: Lỗi khi kiểm tra
```

### API

#### FacebookPermissionChecker

```python
from backend.utils.permission_checker import FacebookPermissionChecker

# Kiểm tra quyền truy cập
result = FacebookPermissionChecker.check_access(
    facebook_id="123456789",
    user_id=1,
    source_type="group",
    user_cookies=None  # Optional
)
# Returns: {
#     'status': PermissionStatus.GRANTED,
#     'message': 'Có quyền truy cập',
#     'accessible': True,
#     'restrictions': [],
#     'checked_at': '2024-04-22T...'
# }

# Kiểm tra quyền hàng loạt
sources = [
    {'facebook_id': '123456', 'source_type': 'group'},
    {'facebook_id': 'mypage', 'source_type': 'page'},
]
results = FacebookPermissionChecker.check_bulk_access(sources, user_id=1)
# Returns: dict mapping facebook_id -> permission result

# Xóa cache
FacebookPermissionChecker.clear_cache()

# Lấy thống kê cache
stats = FacebookPermissionChecker.get_cache_stats()
# Returns: {'cached_items': 5, 'cache_ttl_minutes': 60}
```

#### SourceAccessValidator

```python
from backend.utils.permission_checker import SourceAccessValidator

# Xác thực trước khi lưu
is_valid, error = SourceAccessValidator.validate_before_save(
    facebook_id="123456789",
    source_type="group",
    user_id=1,
    user_cookies=None,
    strict_mode=False
)
# Returns: (True, None) hoặc (False, "Error message")

# Lấy cảnh báo
permission_result = FacebookPermissionChecker.check_access(...)
warning = SourceAccessValidator.get_validation_warning(permission_result)
# Returns: "Group này có thể yêu cầu xác thực" hoặc None
```

---

## 3. Tích Hợp với API

### Tạo Source Với Kiểm Tra Quyền

#### Request
```json
POST /api/sources/
{
    "source_type": "group",
    "facebook_url": "https://www.facebook.com/groups/123456789",
    "include_comments": true,
    "include_replies": true,
    "max_days_old": 30,
    "check_access": true
}
```

#### Response (Thành Công)
```json
{
    "id": 1,
    "user_id": 1,
    "source_type": "group",
    "facebook_id": "123456789",
    "facebook_url": "https://www.facebook.com/groups/123456789",
    "is_active": true,
    "permission_status": "granted",
    "permission_message": "Có quyền truy cập",
    "is_accessible": true,
    "created_at": "2024-04-22T..."
}
```

#### Response (Bị Từ Chối)
```json
{
    "detail": "Access denied: Không có quyền truy cập nguồn này"
}
```

### Kiểm Tra Lại Quyền Truy Cập

```
POST /api/sources/{source_id}/check-access
```

Response:
```json
{
    "source_id": 1,
    "permission_status": "granted",
    "is_accessible": true,
    "message": "Có quyền truy cập",
    "restrictions": []
}
```

---

## 4. Cập Nhật Database

Thêm 5 trường mới vào bảng `sources`:

| Trường | Kiểu | Mô Tả |
|--------|------|-------|
| `permission_status` | Enum | Trạng thái quyền (granted/denied/restricted/not_checked/error) |
| `permission_message` | Text | Thông báo chi tiết |
| `access_restrictions` | Text | JSON list các hạn chế |
| `is_accessible` | Boolean | Có thể truy cập được không |
| `permission_checked_at` | DateTime | Lần cuối kiểm tra |

### Migration

Chạy lệnh sau để cập nhật database:

```bash
alembic revision --autogenerate -m "Add permission fields to Source model"
alembic upgrade head
```

---

## 5. Ví Dụ Sử Dụng

### Ví Dụ 1: Phân Tích URL Và Kiểm Tra Quyền

```python
from backend.utils.facebook_url_parser import FacebookURLParser
from backend.utils.permission_checker import FacebookPermissionChecker

url = "https://www.facebook.com/groups/my-group-123"

# Phân tích URL
parsed = FacebookURLParser.parse(url)
if not parsed['is_valid']:
    print(f"URL không hợp lệ: {parsed['error']}")
else:
    facebook_id = parsed['facebook_id']
    source_type = parsed['source_type']
    
    # Kiểm tra quyền
    permission = FacebookPermissionChecker.check_access(
        facebook_id=facebook_id,
        user_id=1,
        source_type=source_type.value
    )
    
    if permission['accessible']:
        print(f"✓ Có thể truy cập: {permission['message']}")
    else:
        print(f"✗ Không thể truy cập: {permission['message']}")
```

### Ví Dụ 2: Xác Thực Trước Khi Lưu

```python
from backend.utils.permission_checker import SourceAccessValidator

facebook_id = "123456789"
source_type = "group"
user_id = 1

is_valid, error = SourceAccessValidator.validate_before_save(
    facebook_id=facebook_id,
    source_type=source_type,
    user_id=user_id,
    strict_mode=False
)

if is_valid:
    # Lưu source vào database
    save_source(facebook_id, source_type, user_id)
else:
    print(f"Không thể lưu: {error}")
```

### Ví Dụ 3: Kiểm Tra Quyền Hàng Loạt

```python
from backend.utils.permission_checker import FacebookPermissionChecker

sources = [
    {'facebook_id': '123456', 'source_type': 'group'},
    {'facebook_id': 'mypage', 'source_type': 'page'},
    {'facebook_id': 'john_doe', 'source_type': 'user'},
]

results = FacebookPermissionChecker.check_bulk_access(sources, user_id=1)

for source_id, permission_result in results.items():
    status = "✓" if permission_result['accessible'] else "✗"
    print(f"{status} {source_id}: {permission_result['message']}")
```

---

## 6. Caching

### Tính Năng

- Cache kết quả kiểm tra trong 1 giờ
- Giảm số lần gọi kiểm tra quyền
- Tự động hết hạn sau TTL

### Sử Dụng

```python
from backend.utils.permission_checker import FacebookPermissionChecker

# Lần đầu - kiểm tra thực
result1 = FacebookPermissionChecker.check_access(...)  # Kiểm tra từ source

# Lần thứ hai - từ cache
result2 = FacebookPermissionChecker.check_access(...)  # Từ cache
assert result2.get('cached') == True

# Xóa cache
FacebookPermissionChecker.clear_cache()

# Xem thống kê cache
stats = FacebookPermissionChecker.get_cache_stats()
print(f"Cached items: {stats['cached_items']}")
print(f"Cache TTL: {stats['cache_ttl_minutes']} minutes")
```

---

## 7. Test

Chạy các test:

```bash
# Test Facebook URL Parser
pytest backend/tests/test_facebook_url_parser.py -v

# Test Permission Checker
pytest backend/tests/test_permission_checker.py -v

# Test tất cả
pytest backend/tests/ -v
```

---

## 8. Mở Rộng Trong Tương Lai

### Cải Tiến Có Thể Thêm

1. **Xác Thực Với Facebook API**
   - Sử dụng Facebook Graph API để kiểm tra quyền thực tế
   - Yêu cầu access token hợp lệ

2. **Refresh Token**
   - Tự động làm mới token hết hạn
   - Xử lý token expiration

3. **Phát Hiện Loại Source Nâng Cao**
   - Gọi API Facebook để lấy thông tin đúng
   - Xác định chính xác group vs page

4. **Logging & Monitoring**
   - Ghi lại tất cả kiểm tra quyền
   - Theo dõi lỗi quyền truy cập

5. **Webhook Integration**
   - Nhận thông báo khi quyền thay đổi
   - Cập nhật status tự động

---

## 9. Troubleshooting

### Lỗi: "Invalid Facebook URL"
- Kiểm tra URL có phải từ facebook.com không
- Đảm bảo URL không bị cắt ngắn

### Lỗi: "Access denied"
- Kiểm tra xem tài khoản có quyền truy cập không
- Đối với group: cần là thành viên
- Đối với user: cần là bạn bè hoặc hồ sơ công khai

### Permission luôn là "not_checked"
- Đảm bảo `check_access=true` khi tạo source
- Gọi endpoint `/api/sources/{id}/check-access` để cập nhật

---

## 10. Liên Hệ & Hỗ Trợ

Nếu gặp vấn đề hoặc có câu hỏi, vui lòng:
1. Kiểm tra log để tìm chi tiết lỗi
2. Chạy test để đảm bảo module hoạt động
3. Xem các ví dụ trong phần này
