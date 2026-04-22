# Hướng Dẫn Sử Dụng Facebook URL Parser + Permission Checker

## Quick Start

### 1. Tạo Source Với Kiểm Tra Quyền Tự Động

```bash
curl -X POST "http://localhost:8000/api/sources/" \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "group",
    "facebook_url": "https://www.facebook.com/groups/123456789",
    "include_comments": true,
    "include_replies": true,
    "max_days_old": 30,
    "check_access": true
  }'
```

### 2. Kiểm Tra Lại Quyền Truy Cập

```bash
curl -X POST "http://localhost:8000/api/sources/1/check-access" \
  -H "Authorization: Bearer <your-token>"
```

### 3. Xem Thông Tin Chi Tiết Source

```bash
curl -X GET "http://localhost:8000/api/sources/1" \
  -H "Authorization: Bearer <your-token>"
```

---

## Các Bước Thực Hiện

### Bước 1: Cập Nhật Database

Nếu đây là lần đầu tiên sử dụng, cần cập nhật schema database:

```bash
# Tạo migration
cd d:\huan\facebook_post_comment_scraper
alembic revision --autogenerate -m "Add permission fields to sources table"

# Kiểm tra migration
alembic current

# Chạy migration
alembic upgrade head
```

### Bước 2: Khởi Động API

```bash
# Terminal 1: Khởi động API server
cd d:\huan\facebook_post_comment_scraper
python -m uvicorn backend.api.main:app --reload --port 8000
```

### Bước 3: Kiểm Tra URL Parser

```bash
# Terminal 2: Test URL parser
python -c "
from backend.utils.facebook_url_parser import FacebookURLParser

urls = [
    'https://www.facebook.com/groups/123456789',
    'https://www.facebook.com/groups/my-group',
    'https://www.facebook.com/mypage',
    'https://www.facebook.com/profile.php?id=123456789',
]

for url in urls:
    result = FacebookURLParser.parse(url)
    print(f'{url} -> {result[\"facebook_id\"]} ({result[\"source_type\"].value})')
"
```

### Bước 4: Kiểm Tra Permission Checker

```bash
# Test permission checker
python -c "
from backend.utils.permission_checker import FacebookPermissionChecker

result = FacebookPermissionChecker.check_access(
    facebook_id='123456789',
    user_id=1,
    source_type='group'
)

print(f'Status: {result[\"status\"].value}')
print(f'Accessible: {result[\"accessible\"]}')
print(f'Message: {result[\"message\"]}')
"
```

---

## Quy Trình Tạo Source

```
┌─────────────────────────────────────┐
│   User nhập Facebook URL            │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│   FacebookURLParser.parse(url)      │
│   - Validate URL format             │
│   - Extract facebook_id             │
│   - Detect source_type              │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│   FacebookPermissionChecker.        │
│   check_access()                    │
│   - Check public/private access     │
│   - Validate credentials            │
│   - Cache result (1 hour TTL)       │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│   SourceAccessValidator.            │
│   validate_before_save()            │
│   - Final validation                │
│   - Check restrictions              │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│   Save to Database with:            │
│   - permission_status               │
│   - permission_message              │
│   - access_restrictions             │
│   - is_accessible                   │
│   - permission_checked_at           │
└─────────────────────────────────────┘
```

---

## Ví Dụ Thực Tế

### Ví Dụ 1: Tạo Source Từ Group URL

```python
import requests

# Đăng nhập
login_response = requests.post(
    "http://localhost:8000/api/auth/login",
    json={"username": "testuser", "password": "password"}
)
token = login_response.json()["access_token"]

# Tạo source
headers = {"Authorization": f"Bearer {token}"}

response = requests.post(
    "http://localhost:8000/api/sources/",
    headers=headers,
    json={
        "source_type": "group",
        "facebook_url": "https://www.facebook.com/groups/123456789",
        "include_comments": True,
        "check_access": True
    }
)

source = response.json()
print(f"Source created: {source['id']}")
print(f"Permission status: {source.get('permission_status')}")
print(f"Accessible: {source.get('is_accessible')}")
```

### Ví Dụ 2: Xử Lý Lỗi Quyền

```python
import requests

headers = {"Authorization": f"Bearer {token}"}

response = requests.post(
    "http://localhost:8000/api/sources/",
    headers=headers,
    json={
        "source_type": "group",
        "facebook_url": "https://www.facebook.com/groups/private-group",
        "check_access": True
    }
)

if response.status_code == 403:
    error = response.json()
    print(f"Access denied: {error['detail']}")
    # Yêu cầu user cấp quyền
elif response.status_code == 400:
    error = response.json()
    print(f"Invalid URL: {error['detail']}")
else:
    source = response.json()
    print(f"Source created successfully: {source['id']}")
```

### Ví Dụ 3: Kiểm Tra Lại Quyền Định Kỳ

```python
import requests
import json

def refresh_source_permissions(token, source_ids):
    """Làm mới quyền cho nhiều source"""
    headers = {"Authorization": f"Bearer {token}"}
    
    results = {}
    for source_id in source_ids:
        response = requests.post(
            f"http://localhost:8000/api/sources/{source_id}/check-access",
            headers=headers
        )
        
        if response.status_code == 200:
            result = response.json()
            results[source_id] = {
                'status': result['permission_status'],
                'accessible': result['is_accessible'],
                'restrictions': result.get('restrictions', [])
            }
    
    return results

# Sử dụng
source_ids = [1, 2, 3]
permissions = refresh_source_permissions(token, source_ids)

for source_id, perm in permissions.items():
    print(f"Source {source_id}: {perm['status']} - Accessible: {perm['accessible']}")
```

---

## Các Trường Hợp Sử Dụng

### Trường Hợp 1: Public Group
```
URL: https://www.facebook.com/groups/public-gaming
Permission Status: granted
Accessible: true
Message: Group công khai, có thể truy cập
```

### Trường Hợp 2: Private Group (Cần Membership)
```
URL: https://www.facebook.com/groups/123456789
Permission Status: restricted
Accessible: false (nếu không là thành viên)
Restrictions: ["group_member_required"]
Message: Group có thể yêu cầu xác thực
```

### Trường Hợp 3: Public Page
```
URL: https://www.facebook.com/my-brand
Permission Status: granted
Accessible: true
Message: Page công khai, có thể truy cập
```

### Trường Hợp 4: Invalid URL
```
URL: https://www.google.com
Permission Status: error
Accessible: false
Error: URL không phải từ Facebook
```

---

## Troubleshooting

### Problem: "URL không hợp lệ"

**Nguyên nhân:** URL không phải từ facebook.com

**Giải pháp:**
- Kiểm tra URL có chứa `facebook.com` không
- Đảm bảo URL không bị cắt ngắn
- Kiểm tra URL không có ký tự đặc biệt

```python
from backend.utils.facebook_url_parser import FacebookURLParser

url = "..."
is_valid, error = FacebookURLParser.validate_url(url)
if not is_valid:
    print(f"Error: {error}")
```

### Problem: "Access denied"

**Nguyên nhân:** Không có quyền truy cập

**Giải pháp:**
- Kiểm tra tài khoản Facebook có quyền không
- Nếu là group: bạn cần là thành viên
- Nếu là user: bạn cần là bạn bè hoặc hồ sơ công khai

### Problem: Permission luôn là "not_checked"

**Nguyên nhân:** Không kiểm tra quyền khi tạo source

**Giải pháp:**
```python
# Tạo source với check_access=True
{
    "check_access": True  # ← Đảm bảo True
}

# Hoặc kiểm tra sau
requests.post(f"http://localhost:8000/api/sources/{id}/check-access")
```

---

## Performance Tips

### 1. Sử Dụng Batch Checking
```python
# Bad: Kiểm tra từng cái
for url in urls:
    FacebookPermissionChecker.check_access(...)

# Good: Kiểm tra hàng loạt
sources = [{'facebook_id': id, 'source_type': type} for ...]
FacebookPermissionChecker.check_bulk_access(sources, user_id)
```

### 2. Cache Management
```python
# Xem cache stats
stats = FacebookPermissionChecker.get_cache_stats()
print(f"Cached items: {stats['cached_items']}")

# Xóa cache cũ nếu cần
if stats['cached_items'] > 1000:
    FacebookPermissionChecker.clear_cache()
```

### 3. Batch Permission Updates
```bash
# Cron job: Cập nhật quyền mỗi giờ
0 * * * * curl -X POST "http://localhost:8000/api/sources/check-all-access" \
    -H "Authorization: Bearer <admin-token>"
```

---

## Next Steps

1. **Test với dữ liệu thực tế**
   - Thử các URL group khác nhau
   - Kiểm tra các loại quyền truy cập

2. **Tích hợp vào UI**
   - Hiển thị permission status
   - Cho phép kiểm tra lại quyền

3. **Thêm Logging**
   - Ghi chi tiết mỗi kiểm tra quyền
   - Theo dõi các lỗi quyền

4. **Mở Rộng**
   - Tích hợp Facebook Graph API
   - Hỗ trợ refresh token tự động
