import sys
import os
import json
import time
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs
from PyQt6.QtWidgets import (QApplication, QCheckBox, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QComboBox, QSpinBox, QTabWidget,
                             QProgressBar, QGroupBox, QMessageBox, QDialog,
                             QDialogButtonBox, QFrame)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont, QTextCursor

# Import scraper modules
from main import (extract_user_id_from_url, extract_group_id_from_url, 
                 extract_post_id_from_url, fetch_comments_for_post, save_post_data)
from post_scraper import fetch_posts as fetch_page_posts
from group_post_scraper_v2 import fetch_posts as fetch_group_posts
import post_scraper
import group_post_scraper_v2
import single_post_image
import comment_scraper
from proxy_utils import select_proxy
from backend.database.crud import FacebookSessionCRUD
from backend.database.db import SessionLocal


# Cookie Management
def parse_cookies(cookie_string):
    """Parse cookie string in format 'key1=value1;key2=value2' into dictionary"""
    cookies = {}
    if not cookie_string:
        return cookies
    
    # Split by semicolon and parse each cookie
    for cookie in cookie_string.split(';'):
        cookie = cookie.strip()
        if '=' in cookie:
            key, value = cookie.split('=', 1)
            cookies[key.strip()] = value.strip()
    
    return cookies


class CookieDialog(QDialog):
    """Dialog for automated Facebook login to extract cookies and fb_dtsg"""
    
    def __init__(self, parent=None, current_cookies="", current_dtsg=""):
        super().__init__(parent)
        self.setWindowTitle("Configure Cookies & FB_DTSG")
        self.setGeometry(200, 200, 720, 600)
        
        self.cookies_str = current_cookies
        self.dtsg_str = current_dtsg
        self.selected_user_id = None
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Instructions
        instructions = QLabel(
            "🔐 Configure Authentication\n\n"
            "Choose one of the two methods below to extract cookies and fb_dtsg:"
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("background-color: #e3f2fd; padding: 12px; border-radius: 5px; font-size: 13px;")
        layout.addWidget(instructions)

        # --- Method 1: Chrome login ---
        method1_label = QLabel("Method 1 — Automated Chrome Login")
        method1_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #1565C0; margin-top: 6px;")
        layout.addWidget(method1_label)

        chrome_desc = QLabel(
            "Opens Chrome automatically. Login to Facebook, then click OK in the popup."
        )
        chrome_desc.setWordWrap(True)
        chrome_desc.setStyleSheet("font-size: 12px; color: #555; margin-bottom: 4px;")
        layout.addWidget(chrome_desc)

        self.launch_btn = QPushButton("🚀 Launch Chrome & Login")
        self.launch_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 13px;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.launch_btn.clicked.connect(self.launch_chrome_login)
        layout.addWidget(self.launch_btn)

        user_layout = QHBoxLayout()
        user_layout.addWidget(QLabel("App User ID (required to save session):"))
        self.user_id_input = QLineEdit()
        self.user_id_input.setPlaceholderText("e.g. 1")
        self.user_id_input.setMinimumWidth(140)
        user_layout.addWidget(self.user_id_input)
        user_layout.addStretch()
        layout.addLayout(user_layout)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        divider.setStyleSheet("margin: 8px 0;")
        layout.addWidget(divider)

        # --- Method 2: cURL paste ---
        method2_label = QLabel("Method 2 — Paste cURL Command")
        method2_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #6A1B9A; margin-top: 2px;")
        layout.addWidget(method2_label)

        curl_desc = QLabel(
            "In browser DevTools → Network tab, right-click any Facebook GraphQL request "
            "→ Copy → Copy as cURL. Paste it below and click Parse."
        )
        curl_desc.setWordWrap(True)
        curl_desc.setStyleSheet("font-size: 12px; color: #555; margin-bottom: 4px;")
        layout.addWidget(curl_desc)

        self.curl_input = QTextEdit()
        self.curl_input.setPlaceholderText(
            "curl 'https://www.facebook.com/api/graphql/' \\\n"
            "  -H 'accept: */*' \\\n"
            "  -b 'datr=...; c_user=...; xs=...' \\\n"
            "  --data-raw 'fb_dtsg=...&lsd=...'"
        )
        self.curl_input.setMinimumHeight(110)
        self.curl_input.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self.curl_input)

        self.parse_btn = QPushButton("🔍 Parse cURL")
        self.parse_btn.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;
                color: white;
                font-size: 13px;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
        """)
        self.parse_btn.clicked.connect(self.parse_curl_command)
        layout.addWidget(self.parse_btn)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("padding: 10px; font-size: 12px;")
        layout.addWidget(self.status_label)

        # Display extracted values
        self.result_display = QTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setMaximumHeight(100)
        self.result_display.setPlaceholderText("Extracted cookies and fb_dtsg will appear here...")
        layout.addWidget(self.result_display)
        
        # Buttons
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        layout.addWidget(self.buttons)
    
    def launch_chrome_login(self):
        """Launch Chrome with seleniumbase for automated login"""
        try:
            from seleniumbase import SB
            raw_user_id = self.user_id_input.text().strip()
            if not raw_user_id.isdigit():
                QMessageBox.warning(
                    self,
                    "Missing User ID",
                    "Please enter a valid numeric App User ID before launching Chrome.",
                )
                return
            self.selected_user_id = int(raw_user_id)

            self.status_label.setText("🌐 Opening Chrome browser...")
            self.status_label.setStyleSheet("padding: 10px; font-size: 12px; color: #2196F3;")
            self.launch_btn.setEnabled(False)
            QApplication.processEvents()
            
            # Create chrome data directory
            chrome_data_dir = os.path.abspath("chromedata1")
            
            with SB(headless=False, log_cdp_events=True, user_data_dir=chrome_data_dir) as sb:
                sb.open("https://www.facebook.com/")
                
                self.status_label.setText("⏳ Please login to Facebook, then click OK below...")
                self.status_label.setStyleSheet("padding: 10px; font-size: 12px; color: #FF9800;")
                QApplication.processEvents()
                
                # Wait for user to login - show popup dialog
                login_msg = QMessageBox(self)
                login_msg.setIcon(QMessageBox.Icon.Information)
                login_msg.setWindowTitle("Login to Facebook")
                login_msg.setText("🔐 Please complete your Facebook login")
                login_msg.setInformativeText(
                    "A Chrome browser window has been opened.\n\n"
                    "Steps:\n"
                    "1. Login to your Facebook account in the browser\n"
                    "2. Wait for the page to fully load\n"
                    "3. Click OK below to extract cookies and fb_dtsg"
                )
                login_msg.setStandardButtons(QMessageBox.StandardButton.Ok)
                login_msg.setStyleSheet("QLabel{min-width: 400px;}")
                login_msg.exec()
                
                self.status_label.setText("🔍 Extracting cookies and fb_dtsg...")
                self.status_label.setStyleSheet("padding: 10px; font-size: 12px; color: #9C27B0;")
                QApplication.processEvents()
                
                # Get performance logs
                cdp_logs = sb.driver.get_log("performance")
                
                fb_dtsg = None
                for entry in cdp_logs:
                    log = json.loads(entry["message"])["message"]
                    
                    if log["method"] == "Network.requestWillBeSent":
                        request = log["params"]["request"]
                        url = request.get("url", "")
                        
                        if "graphql" in url:
                            post_data = request.get("postData", "")
                            
                            # Extract fb_dtsg using proper URL decoding
                            if post_data and not fb_dtsg:
                                params = parse_qs(post_data)
                                if "fb_dtsg" in params:
                                    fb_dtsg = params["fb_dtsg"][0]
                                    break
                
                # Get cookies
                cookies = sb.get_cookies()
                user_agent = sb.driver.execute_script("return navigator.userAgent") or None
                
                # Convert cookies to semicolon-separated format (for preview/debug)
                cookie_parts = []
                cookies_dict = {}
                for cookie in cookies:
                    cookie_name = str(cookie.get("name", "")).strip()
                    cookie_value = str(cookie.get("value", ""))
                    if cookie_name:
                        cookies_dict[cookie_name] = cookie_value
                        cookie_parts.append(f"{cookie_name}={cookie_value}")
                
                # Add hardcoded static cookies that are always the same
                static_cookies = [
                    "ps_l=1",
                    "ps_n=1",
                    "dpr=1",
                    "ar_debug=1"
                ]
                cookie_parts.extend(static_cookies)
                for item in static_cookies:
                    if "=" not in item:
                        continue
                    key, value = item.split("=", 1)
                    cookies_dict[key.strip()] = value.strip()
                
                self.cookies_str = ";".join(cookie_parts)
                self.cookies_json = json.dumps(cookies_dict, ensure_ascii=False)
                self.dtsg_str = fb_dtsg if fb_dtsg else ""

                save_warning = None
                try:
                    db = SessionLocal()
                    try:
                        FacebookSessionCRUD.upsert_from_login_extraction(
                            db=db,
                            user_id=self.selected_user_id,
                            fb_cookies=self.cookies_json or None,
                            fb_dtsg=self.dtsg_str or None,
                            fb_user_agent=user_agent,
                        )
                    finally:
                        db.close()
                except Exception as db_exc:
                    save_warning = str(db_exc)
                if save_warning:
                    QMessageBox.warning(
                        self,
                        "DB Save Warning",
                        f"Extraction succeeded but failed to save facebook_sessions:\n{save_warning}",
                    )
                
                # Display results
                total_cookies = len(cookies) + len(static_cookies)
                display_text = f"✅ Successfully extracted!\n\n"
                display_text += f"Cookies: {total_cookies} cookies found ({len(cookies)} extracted + {len(static_cookies)} static)\n"
                display_text += f"FB_DTSG: {'Found ✓' if fb_dtsg else 'Not found ✗'}\n\n"
                display_text += f"Preview: {self.cookies_str[:100]}..."
                
                self.result_display.setPlainText(display_text)
                self.status_label.setText("✅ Extraction complete! Click OK to save.")
                self.status_label.setStyleSheet("padding: 10px; font-size: 12px; color: #4CAF50; font-weight: bold;")
                
                # Enable OK button
                self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
                
        except ImportError:
            QMessageBox.critical(
                self,
                "Missing Dependency",
                "SeleniumBase is not installed.\n\nPlease run:\npip install seleniumbase"
            )
            self.launch_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to extract cookies:\n{str(e)}"
            )
            self.status_label.setText(f"❌ Error: {str(e)}")
            self.status_label.setStyleSheet("padding: 10px; font-size: 12px; color: #f44336;")
            self.launch_btn.setEnabled(True)
    
    def parse_curl_command(self):
        """Parse a pasted cURL command and extract cookies and fb_dtsg."""
        from urllib.parse import parse_qs, unquote

        curl_text = self.curl_input.toPlainText().strip()
        if not curl_text:
            self.status_label.setText("⚠️ Please paste a cURL command first.")
            self.status_label.setStyleSheet("padding: 10px; font-size: 12px; color: #FF9800;")
            return

        # --- 1. Extract cookies from -b / --cookie flag ---
        # Handles both single-quoted and double-quoted values, multiline cURL
        cookies_str = ""
        cookie_match = re.search(r"(?:^|\s)-b\s+['\"]([^'\"]+)['\"]", curl_text, re.MULTILINE)
        if cookie_match:
            cookies_str = cookie_match.group(1).strip()

        # --- 2. Extract --data-raw / --data / -d body ---
        body_raw = ""
        data_match = re.search(
            r"(?:--data-raw|--data-urlencode|--data|-d)\s+['\"]([^'\"]+)['\"]",
            curl_text, re.MULTILINE | re.DOTALL
        )
        if data_match:
            body_raw = data_match.group(1).strip()

        # --- 3. Extract fb_dtsg from POST body ---
        fb_dtsg = None
        if body_raw:
            params = parse_qs(body_raw)
            if 'fb_dtsg' in params:
                fb_dtsg = params['fb_dtsg'][0]
            else:
                # fallback: raw regex + manual decode
                m = re.search(r'fb_dtsg=([^&\s]+)', body_raw)
                if m:
                    fb_dtsg = unquote(m.group(1))

        # --- 4. Validate ---
        if not cookies_str and not fb_dtsg:
            self.status_label.setText(
                "❌ Could not find cookies (-b flag) or fb_dtsg (--data-raw) in the pasted cURL."
            )
            self.status_label.setStyleSheet("padding: 10px; font-size: 12px; color: #f44336;")
            return

        self.cookies_str = cookies_str
        self.dtsg_str = fb_dtsg if fb_dtsg else ""

        # --- 5. Show summary ---
        cookie_count = len([c for c in cookies_str.split(';') if '=' in c]) if cookies_str else 0
        display_text = "✅ Successfully extracted from cURL command!\n\n"
        display_text += f"Cookies : {'Found ✓ (' + str(cookie_count) + ' cookies)' if cookies_str else 'Not found ✗'}\n"
        display_text += f"FB_DTSG : {'Found ✓' if fb_dtsg else 'Not found ✗'}\n"
        if fb_dtsg:
            dtsg_preview = fb_dtsg[:40] + ('...' if len(fb_dtsg) > 40 else '')
            display_text += f"          {dtsg_preview}\n"
        if cookies_str:
            display_text += f"\nCookie preview: {cookies_str[:100]}..."

        self.result_display.setPlainText(display_text)
        self.status_label.setText("✅ Parsed successfully! Click OK to save.")
        self.status_label.setStyleSheet(
            "padding: 10px; font-size: 12px; color: #4CAF50; font-weight: bold;"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def get_cookies(self):
        """Get the extracted cookie string"""
        return self.cookies_str

    def get_dtsg(self):
        """Get the extracted fb_dtsg token"""
        return self.dtsg_str


class ScraperThread(QThread):
    """Background thread for scraping operations"""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)  # current, total
    finished_signal = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, scraper_type, params, cookies=None, fb_dtsg=None):
        super().__init__()
        self.scraper_type = scraper_type
        self.params = params
        self.cookies = cookies  # Cookie dictionary
        self.fb_dtsg = fb_dtsg  # FB_DTSG token
        self.proxies = None
    
    def log(self, message):
        """Emit log message"""
        self.log_signal.emit(message)

    def _apply_proxy(self):
        """Select the right proxy type and push it to all scraper modules."""
        has_cookies = bool(self.cookies)
        proxies = select_proxy(has_cookies)

        # Log to UI
        if proxies:
            proxy_url = proxies['http']
            if has_cookies:
                import re as _re
                port = _re.search(r':(\d+)$', proxy_url)
                port_str = port.group(1) if port else '?'
                self.log(f"🔒 Proxy: STATIC (cookie session) — port {port_str}")
            else:
                self.log(f"🔄 Proxy: ROTATING — {proxy_url}")
        else:
            self.log("⚠️  No proxy configured")

        self.proxies = proxies

        # Push to all scraper modules
        comment_scraper.PROXIES     = proxies
        post_scraper.PROXIES        = proxies
        group_post_scraper_v2.PROXIES = proxies
        single_post_image.PROXIES   = proxies

    def run(self):
        """Run the scraping task"""
        try:
            self._apply_proxy()
            if self.scraper_type == "user_posts":
                self.scrape_user_posts()
            elif self.scraper_type == "page_posts":
                self.scrape_page_posts()
            elif self.scraper_type == "group_posts":
                self.scrape_group_posts()
            else:
                self.finished_signal.emit(False, "Invalid scraper type")
        except Exception as e:
            self.finished_signal.emit(False, f"Error: {str(e)}")
    
    def scrape_user_posts(self):
        """Scrape posts from one or more user profile URLs (timeline)"""
        urls = self.params["urls"]  # List of URLs
        count = self.params.get("count")
        last_24_hours_only = self.params.get("last_24_hours_only", False)
        download_media = self.params.get("download_media", False)
        include_comments = self.params.get("include_comments", False)

        total_users = len(urls)
        all_posts_count = 0

        for user_num, url in enumerate(urls, 1):
            self.log(f"\n[User {user_num}/{total_users}] Processing URL: {url}")

            # Extract user ID from URL
            self.log("  Extracting user ID...")
            user_id = extract_user_id_from_url(url, cookies=self.cookies)

            if not user_id:
                self.log("  ❌ Could not extract user ID from URL")
                continue

            self.log(f"  ✅ Extracted User ID: {user_id}")

            try:
                # Update the USER_ID in post_scraper
                post_scraper.USER_ID = user_id
                post_scraper.PAGE_NAME = None
                post_scraper.BASE_HEADERS["referer"] = f"https://www.facebook.com/profile.php?id={user_id}"

                # Update cookies and fb_dtsg in post_scraper if provided
                post_scraper.COOKIES = self.cookies or {}

                if self.fb_dtsg:
                    post_scraper.FB_DTSG = self.fb_dtsg
                    comment_scraper.FB_DTSG = self.fb_dtsg
                else:
                    post_scraper.FB_DTSG = ""
                    comment_scraper.FB_DTSG = ""

                batch_size = 2

                def process_batch(batch_posts, total_so_far, total_limit):
                    total_label = total_limit if total_limit is not None else "24h"
                    self.log(f"  Processing batch of {len(batch_posts)} posts ({total_so_far}/{total_label})...")
                    for i, post in enumerate(batch_posts, 1):
                        post_id = post.get("post_id")
                        if not post_id:
                            self.log(f"    [{i}/{len(batch_posts)}] ⚠️ Skipping post with no ID")
                            continue

                        self.log(f"    [{i}/{len(batch_posts)}] Processing post {post_id}...")

                        try:
                            comments = []
                            if include_comments:
                                comments, _ = fetch_comments_for_post(post_id, cookies=self.cookies)
                            save_post_data(
                                "user_post",
                                post_id,
                                post,
                                comments,
                                include_comments=include_comments,
                            )
                            self.log(f"      ✓ Saved to user_post/{post_id}/{post_id}.json")
                            if include_comments:
                                time.sleep(1)
                        except Exception as e:
                            self.log(f"      ❌ Error fetching comments: {e}")
                            save_post_data(
                                "user_post",
                                post_id,
                                post,
                                [],
                                include_comments=include_comments,
                            )

                fetch_label = "posts from last 24 hours" if last_24_hours_only else f"{count} posts"
                self.log(f"  Fetching {fetch_label} from user {user_id} (batch size: {batch_size})...")
                posts = fetch_page_posts(
                    count,
                    0,
                    batch_size=batch_size,
                    on_batch_complete=process_batch,
                    base_folder="user_post",
                    last_24_hours_only=last_24_hours_only,
                    download_media=download_media,
                )

                self.log(f"  ✓ Completed: {len(posts)} posts processed")
                all_posts_count += len(posts)

            except Exception as e:
                self.log(f"  ❌ Error processing user profile: {e}")
                continue

        self.finished_signal.emit(True, f"Successfully scraped {all_posts_count} posts from {total_users} user profile(s)")
    
    def scrape_page_posts(self):
        """Scrape posts from one or more pages"""
        urls = self.params['urls']  # List of URLs
        count = self.params.get('count')
        last_24_hours_only = self.params.get("last_24_hours_only", False)
        download_media = self.params.get("download_media", False)
        include_comments = self.params.get("include_comments", False)
        
        total_pages = len(urls)
        all_posts_count = 0
        
        for page_num, url in enumerate(urls, 1):
            self.log(f"\n[Page {page_num}/{total_pages}] Processing URL: {url}")
            
            # Extract page ID from URL
            self.log(f"  Extracting page ID...")
            page_id = extract_user_id_from_url(url, cookies=self.cookies)
            
            if not page_id:
                self.log(f"  ❌ Could not extract page ID from URL")
                continue
            
            self.log(f"  ✅ Extracted Page ID: {page_id}")
            
            try:
                # Update the USER_ID in post_scraper
                post_scraper.USER_ID = page_id
                post_scraper.PAGE_NAME = None
                post_scraper.BASE_HEADERS["referer"] = f"https://www.facebook.com/profile.php?id={page_id}"
                
                # Update cookies and fb_dtsg in post_scraper if provided
                if self.cookies:
                    post_scraper.COOKIES = self.cookies
                else:
                    post_scraper.COOKIES = {}
                
                if self.fb_dtsg:
                    post_scraper.FB_DTSG = self.fb_dtsg
                    comment_scraper.FB_DTSG = self.fb_dtsg
                else:
                    post_scraper.FB_DTSG = ""
                    comment_scraper.FB_DTSG = ""
                
                batch_size = 2  # Process in batches of 10
                
                # Define callback to process each batch
                def process_batch(batch_posts, total_so_far, total_limit):
                    total_label = total_limit if total_limit is not None else "24h"
                    self.log(f"  Processing batch of {len(batch_posts)} posts ({total_so_far}/{total_label})...")
                    for i, post in enumerate(batch_posts, 1):
                        post_id = post.get("post_id")
                        if not post_id:
                            self.log(f"    [{i}/{len(batch_posts)}] ⚠️ Skipping post with no ID")
                            continue
                        
                        self.log(f"    [{i}/{len(batch_posts)}] Processing post {post_id}...")
                        
                        try:
                            comments = []
                            if include_comments:
                                comments, _ = fetch_comments_for_post(post_id, cookies=self.cookies)
                            save_post_data(
                                "page_post",
                                post_id,
                                post,
                                comments,
                                include_comments=include_comments,
                            )
                            self.log(f"      ✓ Saved to page_post/{post_id}/{post_id}.json")
                            if include_comments:
                                time.sleep(1)  # Be nice to the server
                        except Exception as e:
                            self.log(f"      ❌ Error fetching comments: {e}")
                            # Save post data even if comments fail
                            save_post_data(
                                "page_post",
                                post_id,
                                post,
                                [],
                                include_comments=include_comments,
                            )
                
                fetch_label = "posts from last 24 hours" if last_24_hours_only else f"{count} posts"
                self.log(f"  Fetching {fetch_label} from page {page_id} (batch size: {batch_size})...")
                posts = fetch_page_posts(
                    count,
                    0,
                    batch_size=batch_size,
                    on_batch_complete=process_batch,
                    base_folder="page_post",
                    last_24_hours_only=last_24_hours_only,
                    download_media=download_media,
                )
                
                self.log(f"  ✓ Completed: {len(posts)} posts processed")
                
                all_posts_count += len(posts)
                
            except Exception as e:
                self.log(f"  ❌ Error processing page: {e}")
                continue
        
        self.finished_signal.emit(True, f"Successfully scraped {all_posts_count} posts from {total_pages} page(s)")
    
    def _scrape_group_posts_sequential(self):
        """Scrape posts from one or more groups"""
        urls = self.params['urls']  # List of URLs
        count = self.params.get('count')
        last_24_hours_only = self.params.get("last_24_hours_only", False)
        download_media = self.params.get("download_media", False)
        include_comments = self.params.get("include_comments", False)
        
        total_groups = len(urls)
        all_posts_count = 0
        
        for group_num, url in enumerate(urls, 1):
            self.log(f"\n[Group {group_num}/{total_groups}] Processing URL: {url}")
            
            # Extract group ID from URL
            self.log(f"  Extracting group ID...")
            group_id = extract_group_id_from_url(url, cookies=self.cookies)
            
            if not group_id:
                self.log(f"  ❌ Could not extract group ID from URL")
                continue
            
            self.log(f"  ✅ Extracted Group ID: {group_id}")
            
            try:
                # Update the GROUP_ID in group_post_scraper_v2
                group_post_scraper_v2.GROUP_ID = group_id
                group_post_scraper_v2.GROUP_NAME = None
                group_post_scraper_v2.HEADERS["referer"] = f"https://www.facebook.com/groups/{group_id}/"
                
                # Update cookies and fb_dtsg in group_post_scraper_v2 if provided
                if self.cookies:
                    group_post_scraper_v2.COOKIES = self.cookies
                else:
                    group_post_scraper_v2.COOKIES = {}
                
                if self.fb_dtsg:
                    group_post_scraper_v2.FB_DTSG = self.fb_dtsg
                    comment_scraper.FB_DTSG = self.fb_dtsg
                else:
                    group_post_scraper_v2.FB_DTSG = ""
                    comment_scraper.FB_DTSG = ""
                
                batch_size = 2  # Process in batches of 10
                
                # Define callback to process each batch
                def process_batch(batch_posts, total_so_far, total_limit):
                    total_label = total_limit if total_limit is not None else "24h"
                    self.log(f"  Processing batch of {len(batch_posts)} posts ({total_so_far}/{total_label})...")
                    for i, post in enumerate(batch_posts, 1):
                        post_id = post.get("post_id")
                        if not post_id:
                            self.log(f"    [{i}/{len(batch_posts)}] ⚠️ Skipping post with no ID")
                            continue
                        
                        self.log(f"    [{i}/{len(batch_posts)}] Processing post {post_id}...")
                        
                        try:
                            comments = []
                            if include_comments:
                                comments, _ = fetch_comments_for_post(post_id, cookies=self.cookies)
                            save_post_data(
                                "group_post",
                                post_id,
                                post,
                                comments,
                                include_comments=include_comments,
                            )
                            self.log(f"      ✓ Saved to group_post/{post_id}/{post_id}.json")
                            if include_comments:
                                time.sleep(1)  # Be nice to the server
                        except Exception as e:
                            self.log(f"      ❌ Error fetching comments: {e}")
                            # Save post data even if comments fail
                            save_post_data(
                                "group_post",
                                post_id,
                                post,
                                [],
                                include_comments=include_comments,
                            )
                
                fetch_label = "posts from last 24 hours" if last_24_hours_only else f"{count} posts"
                self.log(f"  Fetching {fetch_label} from group {group_id} (batch size: {batch_size})...")
                posts = fetch_group_posts(
                    count,
                    0,
                    batch_size=batch_size,
                    on_batch_complete=process_batch,
                    last_24_hours_only=last_24_hours_only,
                    download_media=download_media,
                )
                
                self.log(f"  ✓ Completed: {len(posts)} posts processed")
                
                all_posts_count += len(posts)
                
            except Exception as e:
                self.log(f"  ❌ Error processing group: {e}")
                continue
        
        self.finished_signal.emit(True, f"Successfully scraped {all_posts_count} posts from {total_groups} group(s)")

    def scrape_group_posts(self):
        """Scrape posts from one or more groups with a bounded worker pool."""
        urls = self.params["urls"]
        count = self.params.get("count")
        last_24_hours_only = self.params.get("last_24_hours_only", False)
        download_media = self.params.get("download_media", False)
        include_comments = self.params.get("include_comments", False)
        max_workers = max(1, min(int(self.params.get("max_workers", 3) or 3), 10))
        batch_size = 2

        total_groups = len(urls)
        all_posts_count = 0
        success_count = 0
        error_count = 0

        self.log(f"Running {total_groups} group(s) with {max_workers} worker(s)")

        def process_group(group_num, url, worker_num):
            prefix = f"[Worker {worker_num}] [Group {group_num}/{total_groups}]"
            self.log(f"\n{prefix} Processing URL: {url}")
            self.log(f"{prefix} Extracting group ID...")

            try:
                group_id = extract_group_id_from_url(url, cookies=self.cookies)
            except Exception as e:
                self.log(f"{prefix} Error extracting group ID: {e}")
                return {"ok": False, "posts": 0, "error": str(e)}

            if not group_id:
                self.log(f"{prefix} Could not extract group ID from URL")
                return {"ok": False, "posts": 0, "error": "Could not extract group ID"}

            self.log(f"{prefix} Extracted Group ID: {group_id}")

            def process_batch(batch_posts, total_so_far, total_limit):
                total_label = total_limit if total_limit is not None else "24h"
                self.log(f"{prefix} Processing batch of {len(batch_posts)} posts ({total_so_far}/{total_label})...")

                for i, post in enumerate(batch_posts, 1):
                    post_id = post.get("post_id")
                    if not post_id:
                        self.log(f"{prefix} [{i}/{len(batch_posts)}] Skipping post with no ID")
                        continue

                    self.log(f"{prefix} [{i}/{len(batch_posts)}] Processing post {post_id}...")

                    try:
                        comments = []
                        if include_comments:
                            comments, _ = fetch_comments_for_post(
                                post_id,
                                cookies=self.cookies,
                                fb_dtsg=self.fb_dtsg,
                                proxies=self.proxies,
                            )
                        save_post_data(
                            "group_post",
                            post_id,
                            post,
                            comments,
                            include_comments=include_comments,
                        )
                        self.log(f"{prefix} Saved post {post_id}")
                        if include_comments:
                            time.sleep(1)
                    except Exception as e:
                        self.log(f"{prefix} Error fetching comments for {post_id}: {e}")
                        save_post_data(
                            "group_post",
                            post_id,
                            post,
                            [],
                            include_comments=include_comments,
                        )

            try:
                fetch_label = "posts from last 24 hours" if last_24_hours_only else f"{count} posts"
                self.log(f"{prefix} Fetching {fetch_label} from group {group_id} (batch size: {batch_size})...")
                posts = fetch_group_posts(
                    count,
                    0,
                    batch_size=batch_size,
                    on_batch_complete=process_batch,
                    last_24_hours_only=last_24_hours_only,
                    group_id=group_id,
                    group_name=None,
                    cookies=self.cookies or {},
                    fb_dtsg=self.fb_dtsg or "",
                    proxies=self.proxies,
                    download_media=download_media,
                )
                self.log(f"{prefix} Completed: {len(posts)} posts processed")
                return {"ok": True, "posts": len(posts), "error": None}
            except Exception as e:
                self.log(f"{prefix} Error processing group: {e}")
                return {"ok": False, "posts": 0, "error": str(e)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for group_num, url in enumerate(urls, 1):
                worker_num = ((group_num - 1) % max_workers) + 1
                futures.append(executor.submit(process_group, group_num, url, worker_num))

            completed_groups = 0
            for future in as_completed(futures):
                completed_groups += 1
                try:
                    result = future.result()
                except Exception as e:
                    result = {"ok": False, "posts": 0, "error": str(e)}
                    self.log(f"Worker failed unexpectedly: {e}")

                if result["ok"]:
                    success_count += 1
                    all_posts_count += result["posts"]
                else:
                    error_count += 1
                self.progress_signal.emit(completed_groups, total_groups)

        success = success_count > 0 or total_groups == 0
        self.finished_signal.emit(
            success,
            f"Scraped {all_posts_count} posts from {success_count}/{total_groups} group(s); errors: {error_count}",
        )


class FacebookScraperUI(QMainWindow):
    """Main UI window for Facebook Scraper"""
    
    def __init__(self):
        super().__init__()
        self.scraper_thread = None
        self.cookie_string = ""  # Store raw cookie string
        self.cookies = {}  # Store parsed cookies dictionary
        self.fb_dtsg = ""  # Store fb_dtsg token
        self.init_ui()
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Facebook Scraper")
        self.setGeometry(100, 100, 900, 700)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # Title
        title = QLabel("📘 Facebook Scraper")
        title.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)
        
        # Tab widget for different scraper types
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        # Create tabs
        self.user_posts_tab = self.create_user_posts_tab()
        self.page_posts_tab = self.create_page_posts_tab()
        self.group_posts_tab = self.create_group_posts_tab()
        
        self.tabs.addTab(self.user_posts_tab, "User Posts")
        self.tabs.addTab(self.page_posts_tab, "Page Posts")
        self.tabs.addTab(self.group_posts_tab, "Group Posts")
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)
        
        # Log area
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout()
        log_group.setLayout(log_layout)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        log_layout.addWidget(self.log_text)
        
        clear_log_btn = QPushButton("Clear Log")
        clear_log_btn.clicked.connect(self.clear_log)
        log_layout.addWidget(clear_log_btn)
        
        main_layout.addWidget(log_group)
    
    def create_user_posts_tab(self):
        """Create the User Posts tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        # Cookie button
        cookie_btn = QPushButton("🍪 Configure Cookies & FB_DTSG (Optional)")
        cookie_btn.setStyleSheet("QPushButton { background-color: #9C27B0; color: white; font-size: 12px; padding: 8px; }")
        cookie_btn.clicked.connect(self.configure_cookies)
        layout.addWidget(cookie_btn)
        
        # Input group
        input_group = QGroupBox("User Profile Input (Multiple URLs Supported)")
        input_layout = QVBoxLayout()
        input_group.setLayout(input_layout)
        
        # URL input (textarea for multiple URLs)
        input_layout.addWidget(QLabel("Profile URLs (one per line):"))
        self.user_profile_urls = QTextEdit()
        self.user_profile_urls.setPlaceholderText("https://www.facebook.com/profile.php?id=...\nhttps://www.facebook.com/username\n(one URL per line)")
        self.user_profile_urls.setMaximumHeight(100)
        input_layout.addWidget(self.user_profile_urls)

        # Post count
        # count_layout = QHBoxLayout()
        # count_layout.addWidget(QLabel("Number of posts:"))
        # self.user_post_count = QSpinBox()
        # self.user_post_count.setMinimum(1)
        # self.user_post_count.setMaximum(100000)
        # self.user_post_count.setValue(5)
        # self.user_post_count.setMinimumWidth(150)
        # count_layout.addWidget(self.user_post_count)
        # count_layout.addStretch()
        # input_layout.addLayout(count_layout)

        time_layout = QHBoxLayout()
        self.user_time_filter_check = QCheckBox("Last 24 hours only")
        self.user_time_filter_check.setChecked(True)
        time_layout.addWidget(self.user_time_filter_check)
        time_layout.addStretch()
        input_layout.addLayout(time_layout)

        media_layout = QHBoxLayout()
        self.user_download_media_check = QCheckBox("Download images locally")
        self.user_download_media_check.setChecked(False)
        self.user_download_media_check.setToolTip("When disabled, image metadata is still scraped but image files are not saved locally.")
        media_layout.addWidget(self.user_download_media_check)
        media_layout.addStretch()
        input_layout.addLayout(media_layout)

        # Comment fetching
        comment_layout = QHBoxLayout()
        self.user_include_comments_check = QCheckBox("Fetch comments")
        self.user_include_comments_check.setChecked(False)
        self.user_include_comments_check.setToolTip("When selected, fetch and save comments for each scraped post.")
        comment_layout.addWidget(self.user_include_comments_check)
        comment_layout.addStretch()
        input_layout.addLayout(comment_layout)
        
        layout.addWidget(input_group)
        
        # Scrape button
        scrape_btn = QPushButton("🚀 Scrape User Posts")
        scrape_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-size: 14px; padding: 10px; }")
        scrape_btn.clicked.connect(self.scrape_user_posts)
        layout.addWidget(scrape_btn)
        
        layout.addStretch()
        return tab
    
    def create_page_posts_tab(self):
        """Create the Page Posts tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        # Cookie button
        cookie_btn = QPushButton("🍪 Configure Cookies & FB_DTSG (Optional)")
        cookie_btn.setStyleSheet("QPushButton { background-color: #9C27B0; color: white; font-size: 12px; padding: 8px; }")
        cookie_btn.clicked.connect(self.configure_cookies)
        layout.addWidget(cookie_btn)
        
        # Input group
        input_group = QGroupBox("Page Input (Multiple URLs Supported)")
        input_layout = QVBoxLayout()
        input_group.setLayout(input_layout)
        
        # URL input (textarea for multiple URLs)
        input_layout.addWidget(QLabel("Page URLs (one per line):"))
        self.page_urls = QTextEdit()
        self.page_urls.setPlaceholderText("https://www.facebook.com/profile.php?id=...\nhttps://www.facebook.com/...\n(one URL per line)")
        self.page_urls.setMaximumHeight(100)
        input_layout.addWidget(self.page_urls)
        
        # Post count
        # count_layout = QHBoxLayout()
        # count_layout.addWidget(QLabel("Number of posts:"))
        # self.page_post_count = QSpinBox()
        # self.page_post_count.setMinimum(1)
        # self.page_post_count.setMaximum(100000)
        # self.page_post_count.setValue(5)
        # self.page_post_count.setMinimumWidth(150)
        # count_layout.addWidget(self.page_post_count)
        # count_layout.addStretch()
        # input_layout.addLayout(count_layout)

        # Time count
        time_layout = QHBoxLayout()
        self.page_time_filter_check = QCheckBox("Last 24 hours only")
        self.page_time_filter_check.setChecked(True)
        time_layout.addWidget(self.page_time_filter_check)
        time_layout.addStretch()
        input_layout.addLayout(time_layout)

        media_layout = QHBoxLayout()
        self.page_download_media_check = QCheckBox("Download images locally")
        self.page_download_media_check.setChecked(False)
        self.page_download_media_check.setToolTip("When disabled, image metadata is still scraped but image files are not saved locally.")
        media_layout.addWidget(self.page_download_media_check)
        media_layout.addStretch()
        input_layout.addLayout(media_layout)

        # Comment fetching
        comment_layout = QHBoxLayout()
        self.page_include_comments_check = QCheckBox("Fetch comments")
        self.page_include_comments_check.setChecked(False)
        self.page_include_comments_check.setToolTip("When selected, fetch and save comments for each scraped post.")
        comment_layout.addWidget(self.page_include_comments_check)
        comment_layout.addStretch()
        input_layout.addLayout(comment_layout)
        
        layout.addWidget(input_group)
        
        # Scrape button
        scrape_btn = QPushButton("🚀 Scrape Page Posts")
        scrape_btn.setStyleSheet("QPushButton { background-color: #2196F3; color: white; font-size: 14px; padding: 10px; }")
        scrape_btn.clicked.connect(self.scrape_page_posts)
        layout.addWidget(scrape_btn)
        
        layout.addStretch()
        return tab
    
    def create_group_posts_tab(self):
        """Create the Group Posts tab"""
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        # Cookie button
        cookie_btn = QPushButton("🍪 Configure Cookies & FB_DTSG (Optional)")
        cookie_btn.setStyleSheet("QPushButton { background-color: #9C27B0; color: white; font-size: 12px; padding: 8px; }")
        cookie_btn.clicked.connect(self.configure_cookies)
        layout.addWidget(cookie_btn)
        
        # Input group
        input_group = QGroupBox("Group Input (Multiple URLs Supported)")
        input_layout = QVBoxLayout()
        input_group.setLayout(input_layout)
        
        # URL input (textarea for multiple URLs)
        input_layout.addWidget(QLabel("Group URLs (one per line):"))
        self.group_urls = QTextEdit()
        self.group_urls.setPlaceholderText("https://web.facebook.com/groups/668881464321714/\nhttps://www.facebook.com/groups/...\n(one URL per line)")
        self.group_urls.setMaximumHeight(100)
        input_layout.addWidget(self.group_urls)
        
        # Post count
        # count_layout = QHBoxLayout()
        # count_layout.addWidget(QLabel("Number of posts:"))
        # self.group_post_count = QSpinBox()
        # self.group_post_count.setMinimum(1)
        # self.group_post_count.setMaximum(10000)
        # self.group_post_count.setValue(5)
        # self.group_post_count.setMinimumWidth(150)
        # count_layout.addWidget(self.group_post_count)
        # count_layout.addStretch()
        # input_layout.addLayout(count_layout)

        time_layout = QHBoxLayout()
        self.group_time_filter_check = QCheckBox("Last 24 hours only")
        self.group_time_filter_check.setChecked(True) # set mặc định là chỉ lấy bài viết trong 24 giờ qua
        time_layout.addWidget(self.group_time_filter_check)
        time_layout.addStretch()
        input_layout.addLayout(time_layout)

        media_layout = QHBoxLayout()
        self.group_download_media_check = QCheckBox("Download images locally")
        self.group_download_media_check.setChecked(False) # set trạng thái mặc định là không tải ảnh về
        self.group_download_media_check.setToolTip("When disabled, image metadata is still scraped but image files are not saved locally.")
        media_layout.addWidget(self.group_download_media_check)
        media_layout.addStretch()
        input_layout.addLayout(media_layout)
        
        # Comment fetching
        comment_layout = QHBoxLayout()
        self.group_include_comments_check = QCheckBox("Fetch comments")
        self.group_include_comments_check.setChecked(False)
        self.group_include_comments_check.setToolTip("When selected, fetch and save comments for each scraped post.")
        comment_layout.addWidget(self.group_include_comments_check)
        comment_layout.addStretch()
        input_layout.addLayout(comment_layout)

        worker_layout = QHBoxLayout()
        worker_layout.addWidget(QLabel("Workers:"))
        self.group_max_workers = QSpinBox()
        self.group_max_workers.setMinimum(1)
        self.group_max_workers.setMaximum(10)
        self.group_max_workers.setValue(3)
        self.group_max_workers.setMinimumWidth(150)
        self.group_max_workers.setToolTip("Maximum number of groups to scrape in parallel.")
        worker_layout.addWidget(self.group_max_workers)
        worker_layout.addStretch()
        input_layout.addLayout(worker_layout)
        
        layout.addWidget(input_group)
        
        # Scrape button
        scrape_btn = QPushButton("🚀 Scrape Group Posts")
        scrape_btn.setStyleSheet("QPushButton { background-color: #FF9800; color: white; font-size: 14px; padding: 10px; }")
        scrape_btn.clicked.connect(self.scrape_group_posts)
        layout.addWidget(scrape_btn)
        
        layout.addStretch()
        return tab
    
    def scrape_user_posts(self):
        """Start scraping posts from user profile URLs"""
        urls_text = self.user_profile_urls.toPlainText().strip()
        last_24_hours_only = self.user_time_filter_check.isChecked()
        download_media = self.user_download_media_check.isChecked()
        include_comments = self.user_include_comments_check.isChecked()
        count = None if last_24_hours_only else 10
        
        if not urls_text:
            self.show_error("Please enter profile URLs")
            return
        
        # Parse URLs
        urls = [url.strip() for url in urls_text.split('\n') if url.strip()]
        
        if not urls:
            self.show_error("No valid URLs found")
            return
        
        # Start scraping in background thread
        fetch_label = "posts from last 24 hours" if last_24_hours_only else f"{count} posts"
        media_mode = "on" if download_media else "off"
        comment_mode = "on" if include_comments else "off"
        self.log(f"Starting user posts scraper for {len(urls)} profile(s) (fetching {fetch_label} each, comments: {comment_mode}, image download: {media_mode})...")
        params = {
            "urls": urls,
            "count": count,
            "include_comments": include_comments,
            "last_24_hours_only": last_24_hours_only,
            "download_media": download_media,
        }
        self.start_scraping("user_posts", params)
    
    def scrape_page_posts(self):
        """Start scraping posts from page URLs"""
        urls_text = self.page_urls.toPlainText().strip()
        last_24_hours_only = self.page_time_filter_check.isChecked()
        download_media = self.page_download_media_check.isChecked()
        include_comments = self.page_include_comments_check.isChecked()
        count = None if last_24_hours_only else 10
        
        if not urls_text:
            self.show_error("Please enter page URLs")
            return
        
        # Parse URLs
        urls = [url.strip() for url in urls_text.split('\n') if url.strip()]
        
        if not urls:
            self.show_error("No valid URLs found")
            return
        
        # Start scraping in background thread
        fetch_label = "posts from last 24 hours" if last_24_hours_only else f"{count} posts"
        media_mode = "on" if download_media else "off"
        comment_mode = "on" if include_comments else "off"
        self.log(f"Starting page posts scraper for {len(urls)} page(s) (fetching {fetch_label} each, comments: {comment_mode}, image download: {media_mode})...")
        params = {
            'urls': urls,
            'count': count,
            'include_comments': include_comments,
            "last_24_hours_only": last_24_hours_only,
            "download_media": download_media,
        }
        self.start_scraping("page_posts", params)
    
    def scrape_group_posts(self):
        """Start scraping posts from group URLs"""
        urls_text = self.group_urls.toPlainText().strip()
        last_24_hours_only = self.group_time_filter_check.isChecked()
        download_media = self.group_download_media_check.isChecked()
        include_comments = self.group_include_comments_check.isChecked()
        count = None if last_24_hours_only else 10
        max_workers = self.group_max_workers.value()
        
        if not urls_text:
            self.show_error("Please enter group URLs")
            return
        
        # Parse URLs
        urls = [url.strip() for url in urls_text.split('\n') if url.strip()]
        
        if not urls:
            self.show_error("No valid URLs found")
            return
        
        # Start scraping in background thread
        fetch_label = "posts from last 24 hours" if last_24_hours_only else f"{count} posts"
        media_mode = "on" if download_media else "off"
        comment_mode = "on" if include_comments else "off"
        self.log(f"Starting group posts scraper for {len(urls)} group(s) with {max_workers} worker(s) (fetching {fetch_label} each, comments: {comment_mode}, image download: {media_mode})...")
        params = {
            'urls': urls,
            'count': count,
            'include_comments': include_comments,
            "last_24_hours_only": last_24_hours_only,
            "max_workers": max_workers,
            "download_media": download_media,
        }
        self.start_scraping("group_posts", params)
    
    def start_scraping(self, scraper_type, params):
        """Start the scraping thread"""
        if self.scraper_thread and self.scraper_thread.isRunning():
            self.show_error("A scraping task is already running. Please wait.")
            return
        
        # Log configuration status
        config_items = []
        if self.cookies:
            config_items.append(f"{len(self.cookies)} cookies")
        if self.fb_dtsg:
            config_items.append("fb_dtsg token")
        
        if config_items:
            self.log(f"Using {' + '.join(config_items)} for authenticated session")
        else:
            self.log("No authentication configured - using unauthenticated requests")
        
        # Create and start thread
        self.scraper_thread = ScraperThread(scraper_type, params, self.cookies, self.fb_dtsg)
        self.scraper_thread.log_signal.connect(self.log)
        self.scraper_thread.progress_signal.connect(self.update_progress)
        self.scraper_thread.finished_signal.connect(self.scraping_finished)
        
        # Disable UI
        self.tabs.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        self.scraper_thread.start()
    
    def scraping_finished(self, success, message):
        """Handle scraping completion"""
        # Re-enable UI
        self.tabs.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        if success:
            self.log(f"✅ {message}")
            QMessageBox.information(self, "Success", message)
        else:
            self.log(f"❌ {message}")
            self.show_error(message)
    
    def update_progress(self, current, total):
        """Update progress bar"""
        if total > 0:
            progress = int((current / total) * 100)
            self.progress_bar.setValue(progress)
    
    def log(self, message):
        """Add message to log"""
        self.log_text.append(message)
        # Auto-scroll to bottom
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)
    
    def clear_log(self):
        """Clear the log"""
        self.log_text.clear()
    
    def configure_cookies(self):
        """Open cookie configuration dialog"""
        dialog = CookieDialog(self, self.cookie_string, self.fb_dtsg)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.cookie_string = dialog.get_cookies()
            self.cookies = parse_cookies(self.cookie_string)
            self.fb_dtsg = dialog.get_dtsg()
            
            config_items = []
            if self.cookies:
                config_items.append(f"{len(self.cookies)} cookies")
            if self.fb_dtsg:
                config_items.append("fb_dtsg token")
            
            if config_items:
                self.log(f"✅ Configured {' and '.join(config_items)}")
                message = "Successfully configured:\n\n"
                if self.cookies:
                    message += f"• {len(self.cookies)} cookies: {', '.join(list(self.cookies.keys())[:5])}{'...' if len(self.cookies) > 5 else ''}\n"
                if self.fb_dtsg:
                    dtsg_preview = self.fb_dtsg[:30] + "..." if len(self.fb_dtsg) > 30 else self.fb_dtsg
                    message += f"• fb_dtsg: {dtsg_preview}\n"
                QMessageBox.information(self, "Configuration Complete", message)
            else:
                self.log("⚠️ Configuration cleared")
                QMessageBox.information(self, "Configuration Cleared", "Cookies and fb_dtsg have been cleared.")
    
    def show_error(self, message):
        """Show error message"""
        QMessageBox.critical(self, "Error", message)
        self.log(f"❌ {message}")


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern style
    
    window = FacebookScraperUI()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
