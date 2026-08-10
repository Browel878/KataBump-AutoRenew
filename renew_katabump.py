#!/usr/bin/env python3
"""
KataBump 自动续期脚本 (基于 undetected-chromedriver)

参考: peiqzh/Auto-Renew-Katabump + liveqte/Auto-Renew-Katabump
核心: uc 绕过 Turnstile + Xvfb 有头模式 + Altcha 弹窗验证

流程:
1. uc.Chrome (HEADLESS=false + Xvfb) → 不被 Turnstile 检测
2. 填表 + ActionChains 偏移点击 Turnstile
3. 点击 See → 检查到期日 → Renew → Altcha checkbox → 提交
4. TG 通知结果
"""

import os, sys, time, logging, random, re, json, subprocess
from datetime import datetime, timezone, timedelta

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, WebDriverException

# ===================== 配置 =====================
HEADLESS = os.getenv('HEADLESS', 'false').lower() == 'true'
ACCOUNTS_ENV = os.getenv('ACCOUNTS', os.getenv('USERS_JSON', ''))
# 代理: NODE_LINK 代理由 workflow 的 setup_proxy.sh 生成 sing-box 配置后写入
# IS_PROXY=true / PROXY_SERVER=socks5://127.0.0.1:1080 环境变量；HTTP_PROXY 作为兼容后备
IS_PROXY = os.getenv('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER = (os.getenv('PROXY_SERVER', '') or os.getenv('HTTP_PROXY', '') or '').strip()
if IS_PROXY and not PROXY_SERVER:
    PROXY_SERVER = 'http://127.0.0.1:1081'
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN', os.getenv('BOT_TOKEN', ''))
TG_CHAT_ID = os.getenv('TG_CHAT_ID', os.getenv('CHAT_ID', ''))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===================== 工具 =====================
def rand_int(a, b): return random.randint(a, b)
def sleep_ms(ms): time.sleep(ms / 1000)
def human_delay(): sleep_ms(7000 + random.random() * 5000)

def human_type(driver, selector, text):
    try:
        el = WebDriverWait(driver, 15).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))
        el.clear()
        for ch in text:
            el.send_keys(ch)
            sleep_ms(rand_int(50, 150))
        return True
    except Exception as e:
        logger.warning(f"打字失败: {e}")
        return False

def mask_email(email):
    try:
        if '@' in email:
            p, d = email.split('@', 1)
            return f"{p[0]}***@{d}" if len(p) > 2 else f"{p}***@{d}"
        return f"{email[0]}***"
    except:
        return "User"

# ===================== TG 通知 =====================
def send_tg(text, photo_path=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    tz = timezone(timedelta(hours=8))
    ts = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    full = f"🔄 KataBump 续期通知\n\n时间: {ts}\n\n{text}"
    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, 'rb') as f:
                requests.post(
                    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
                    data={"chat_id": TG_CHAT_ID, "caption": full},
                    files={'photo': f}, timeout=20)
        else:
            requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                data={"chat_id": TG_CHAT_ID, "text": full}, timeout=10)
    except Exception as e:
        logger.warning(f"TG 发送失败: {e}")

# ===================== 核心 =====================
class KataBumpRenew:
    def __init__(self, user, password):
        self.user = user
        self.password = password
        self.masked = mask_email(user)
        self.driver = None
        self.screenshot_path = None

    def setup_driver(self):
        """每次调用都创建新的 Options (uc 不允许重用)"""
        opts = Options()
        if HEADLESS:
            opts.add_argument('--headless')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--disable-blink-features=AutomationControlled')
        opts.add_argument('--remote-debugging-port=9222')
        if PROXY_SERVER:
            logger.info(f"🔗 挂载代理: {PROXY_SERVER}")
            opts.add_argument(f'--proxy-server={PROXY_SERVER}')
        else:
            logger.info("🌐 未使用代理，直连访问")

        v_env = os.getenv('CHROME_VERSION', '')
        v_main = int(v_env) if v_env.isdigit() else None
        logger.info(f"🛠️ 驱动初始化 - 版本: {v_main or '自动'}")

        for v in [v_main, None]:
            try:
                self.driver = uc.Chrome(options=opts, headless=HEADLESS,
                                        version_main=v, use_subprocess=True)
                self.driver.set_window_size(1280, 720)
                return
            except Exception as e:
                if self.driver:
                    try: self.driver.quit()
                    except: pass
                    self.driver = None
                # uc 不允许重用 Options，重试时创建新的
                opts = Options()
                if HEADLESS:
                    opts.add_argument('--headless')
                opts.add_argument('--no-sandbox')
                opts.add_argument('--disable-dev-shm-usage')
                opts.add_argument('--disable-blink-features=AutomationControlled')
                opts.add_argument('--remote-debugging-port=9222')
                if PROXY_SERVER:
                    opts.add_argument(f'--proxy-server={PROXY_SERVER}')
                if v is None:
                    raise

    def _turnstile_solved(self):
        """检测 Turnstile 是否已通过: 任一 turnstile 隐藏输入有 token / 复选框已勾选"""
        try:
            solved = self.driver.execute_script("""
            (function(){
                // 1) 隐藏 token 输入（兼容自定义 response field name）
                var inputs = document.querySelectorAll(
                    'input[name*="cf-turnstile"], input[name*="turnstile"]');
                for (var i = 0; i < inputs.length; i++) {
                    if (inputs[i].value && inputs[i].value.length > 20) return true;
                }
                // 2) widget 内复选框已勾选
                var w = document.querySelector('.cf-turnstile input[type="checkbox"]');
                if (w && w.checked) return true;
                var w2 = document.querySelector('.cf-turnstile input[aria-checked="true"]');
                if (w2) return true;
                return false;
            })()
            """)
            return bool(solved)
        except Exception:
            return False

    def _turnstile_debug(self):
        """Turnstile 未通过时输出 DOM 快照，便于排查"""
        try:
            info = self.driver.execute_script("""
            (function(){
                var out = {url: location.href, widget: null, inputs: [], iframes: []};
                var w = document.querySelector('.cf-turnstile');
                if (w) out.widget = w.outerHTML.slice(0, 1200);
                document.querySelectorAll('input').forEach(function(i){
                    if (/turnstile|captcha/i.test(i.name || '')) {
                        out.inputs.push({name: i.name, valueLen: (i.value || '').length});
                    }
                });
                document.querySelectorAll('iframe').forEach(function(f){
                    out.iframes.push((f.src || '').slice(0, 120));
                });
                return out;
            })()
            """)
            logger.warning(f"🔍 {self.masked} Turnstile DOM 快照: "
                           f"{json.dumps(info, ensure_ascii=False)[:2000]}")
        except Exception as e:
            logger.warning(f"🔍 {self.masked} Turnstile DOM 快照失败: {e}")

    def _expand_turnstile(self):
        """展开 Turnstile widget，防止被 overflow:hidden 父容器裁剪"""
        try:
            self.driver.execute_script("""
            (function() {
                var w = document.querySelector('.cf-turnstile');
                if (!w) return 'no-widget';
                var el = w;
                for (var i = 0; i < 20; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    var s = window.getComputedStyle(el);
                    if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
                        el.style.overflow = 'visible';
                    el.style.minWidth = 'max-content';
                }
                return 'done';
            })()
            """)
        except Exception as e:
            logger.warning(f"⚠️ {self.masked} 展开 Turnstile 失败: {e}")

    def _click_turnstile_checkbox(self):
        """点击 Turnstile 复选框: 优先 iframe 真实坐标，退回容器偏移"""
        for sel in [".cf-turnstile iframe[src*='challenges.cloudflare.com']",
                    ".cf-turnstile iframe",
                    "iframe[src*='challenges.cloudflare.com']"]:
            try:
                iframe = self.driver.find_element(By.CSS_SELECTOR, sel)
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", iframe)
                sleep_ms(300 + random.random() * 300)
                r = iframe.rect
                # 复选框在 iframe 左侧，flexible 模式下 iframe 可能很宽，坐标封顶 45px
                cx = min(45, r['width'] * 0.12) + random.uniform(-4, 4)
                cy = r['height'] / 2 + random.uniform(-4, 4)
                actions = ActionChains(self.driver)
                actions.move_to_element_with_offset(iframe, cx, cy)
                actions.pause(random.uniform(0.4, 0.7))
                actions.click_and_hold()
                actions.pause(random.uniform(0.1, 0.25))
                actions.release()
                actions.perform()
                logger.info(f"🖱️ {self.masked} Turnstile iframe 坐标点击 ({sel}) ({cx:.0f}, {cy:.0f})")
                return True
            except Exception:
                continue

        # 兜底: 容器偏移点击（目标为容器左缘 ~30px 复选框位置）
        try:
            container = self.driver.find_element(By.CLASS_NAME, "cf-turnstile")
            size = container.size
            base_x = 30 - (size['width'] / 2)
            rand_x = base_x + random.uniform(-5, 5)
            rand_y = random.uniform(-5, 5)
            actions = ActionChains(self.driver)
            actions.move_to_element(container)
            actions.pause(random.uniform(0.5, 0.8))
            actions.move_to_element_with_offset(container, rand_x, rand_y)
            actions.click_and_hold()
            actions.pause(random.uniform(0.1, 0.25))
            actions.release()
            actions.perform()
            logger.info(f"🖱️ {self.masked} Turnstile 容器偏移点击")
            return True
        except Exception as e:
            logger.error(f"❌ {self.masked} Turnstile 点击失败: {e}")
            return False

    def _handle_turnstile(self, context="", max_attempts=6):
        """Cloudflare Turnstile — 展开 widget + 重试点击复选框"""
        try:
            container = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, "cf-turnstile")))

            # 静默通过直接返回
            if self._turnstile_solved():
                logger.info(f"✅ {self.masked} [{context}] Turnstile 已静默通过")
                sleep_ms(1500 + random.random() * 1000)
                return True

            self._expand_turnstile()

            for attempt in range(max_attempts):
                if self._turnstile_solved():
                    logger.info(f"✅ {self.masked} [{context}] Turnstile 通过 (第 {attempt+1} 次)")
                    sleep_ms(1500 + random.random() * 1000)
                    return True

                self._expand_turnstile()
                self._click_turnstile_checkbox()

                # 轮询 token
                for _ in range(8):
                    if self._turnstile_solved():
                        logger.info(f"✅ {self.masked} [{context}] Turnstile 通过 (第 {attempt+1} 次)")
                        sleep_ms(1500 + random.random() * 1000)
                        return True
                    sleep_ms(1000)

            self._turnstile_debug()
            logger.warning(f"⚠️ {self.masked} [{context}] Turnstile {max_attempts} 次尝试均超时")
            return False
        except Exception as e:
            logger.error(f"❌ {self.masked} [{context}] Turnstile 失败: {e}")
            return False

    def _handle_altcha(self):
        """续期弹窗的 Altcha 验证 — checkbox click"""
        try:
            checkbox = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//div[@class='altcha']//input[@type='checkbox' and @required]")))
            logger.info(f"✅ {self.masked} 找到 Altcha 复选框")
            checkbox.click()
            sleep_ms(8000 + random.random() * 2000)
        except TimeoutException:
            logger.warning("⚠️ 未找到 Altcha 复选框 (可能不需要)")

    def process(self):
        """主续期流程"""
        logger.info(f"🚀 登录: {self.masked}")
        self.driver.get("https://dashboard.katabump.com/auth/login")
        sleep_ms(5000 + random.random() * 2000)

        # 填表
        logger.info(f"📝 {self.masked} 填写邮箱...")
        if not human_type(self.driver, "input#email", self.user):
            raise Exception("未找到邮箱输入框")
        sleep_ms(2000 + random.random() * 1000)

        logger.info(f"🔒 {self.masked} 填写密码...")
        if not human_type(self.driver, "input#password", self.password):
            raise Exception("未找到密码输入框")
        sleep_ms(2000 + random.random() * 1000)

        # Turnstile（失败则中止登录）
        if not self._handle_turnstile("Login"):
            raise Exception("Turnstile 验证未通过，无法登录")

        # 登录
        logger.info(f"📤 {self.masked} 提交登录...")
        self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        human_delay()

        # 检查是否还在登录页
        if "login" in self.driver.current_url:
            raise Exception("登录失败 — 仍在登录页")

        # 进入服务器详情
        logger.info(f"🎯 {self.masked} 进入服务器页...")
        manage_btn = WebDriverWait(self.driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'See')]")))
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", manage_btn)
        sleep_ms(1000 + random.random() * 1000)
        self.driver.execute_script("arguments[0].click();", manage_btn)
        human_delay()

        # 检查到期日
        logger.info(f"📅 {self.masked} 检查到期日...")
        try:
            expiry_el = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//div[contains(text(), 'Expiry')]/following-sibling::div")))
            expiry_text = expiry_el.text.strip()
            logger.info(f"⌛ {self.masked} 到期: {expiry_text}")

            tz_hkt = timezone(timedelta(hours=8))
            today = datetime.now(tz_hkt).date()
            expiry_date = None
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"]:
                try:
                    expiry_date = datetime.strptime(expiry_text, fmt).date()
                    break
                except ValueError:
                    continue

            if expiry_date:
                days_diff = (expiry_date - today).days
                if days_diff > 1:
                    notice = f"⏰ {self.masked}\n📅 未到续期日: {expiry_text}\n🔄 剩余 {days_diff} 天"
                    logger.info(f"ℹ️ {notice}")
                    return True, notice
                elif days_diff < 0:
                    notice = f"⚠️ {self.masked}\n📅 已过期 {abs(days_diff)} 天: {expiry_text}\n⚠️ 可能已被删除!"
                    logger.warning(notice)
                    return False, notice
        except Exception as e:
            logger.warning(f"⚠️ 日期检查异常: {e}，继续续期")

        # 点击 Renew
        logger.info(f"🔄 {self.masked} 续期流程...")
        try:
            renew_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Renew')]")))
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", renew_btn)
            self.driver.execute_script("arguments[0].click();", renew_btn)
            logger.info(f"📑 {self.masked} 打开 Renew 弹窗")
        except Exception as e:
            raise Exception(f"无法打开 Renew 弹窗: {e}")

        sleep_ms(2000 + random.random() * 1000)

        # Altcha
        self._handle_altcha()

        # 最终 Renew
        try:
            confirm = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//div[@id='renew-modal']//button[@type='submit' and contains(text(), 'Renew')]")))
            self.driver.execute_script("arguments[0].click();", confirm)
        except Exception as e:
            raise Exception(f"弹窗提交失败: {e}")

        sleep_ms(7000 + random.random() * 2000)

        # 结果核验
        try:
            alerts = self.driver.find_elements(By.CSS_SELECTOR, ".alert-danger")
            if alerts and alerts[0].is_displayed():
                msg = alerts[0].text.strip().replace('×', '')
                return False, f"⚠️ {self.masked}\n续期失败: {msg}"

            final_el = self.driver.find_element(
                By.XPATH, "//div[contains(text(), 'Expiry')]/following-sibling::div")
            final = final_el.text.strip()
            logger.info(f"✅ {self.masked} 续期后到期: {final}")
            if final and final != expiry_text:
                return True, f"✅ {self.masked}\n🎉 续期成功!\n📅 新到期: {final}"
            else:
                return False, f"⚠️ {self.masked}\n时间未更新 ({final})"
        except Exception as e:
            return False, f"❌ {self.masked}\n验证结果异常: {e}"

    def run(self):
        max_retries = 3
        last_error = ""
        for attempt in range(max_retries):
            try:
                if not self.driver:
                    self.setup_driver()
                if attempt > 0:
                    logger.info(f"🔄 {self.masked} 第 {attempt+1} 次尝试...")
                    # 关闭旧 driver，重新创建
                    try: self.driver.quit()
                    except: pass
                    self.driver = None
                    self.setup_driver()
                    self.driver.get("https://dashboard.katabump.com/auth/login")
                    sleep_ms(5000 + random.random() * 3000)
                success, msg = self.process()
                if success:
                    return True, msg
                last_error = msg
                if "续期失败" in msg or "已过期" in msg:
                    break
            except Exception as e:
                last_error = str(e)[:80]
                logger.error(f"❌ {self.masked} 第 {attempt+1} 次: {e}")
                # 失败时先截图再关闭驱动
                if self.driver:
                    try:
                        self.screenshot_path = f"error-{self.user.split('@')[0]}.png"
                        self.driver.save_screenshot(self.screenshot_path)
                    except Exception:
                        pass
                    try: self.driver.quit()
                    except: pass
                    self.driver = None
                if attempt < max_retries - 1:
                    sleep_ms(5000 + random.random() * 5000)

        # 兜底截图（非异常失败路径）
        if not self.screenshot_path and self.driver:
            try:
                self.screenshot_path = f"error-{self.user.split('@')[0]}.png"
                self.driver.save_screenshot(self.screenshot_path)
            except Exception:
                pass
        return False, f"❌ {self.masked}\n{max_retries} 次尝试均失败\n{last_error}"


# ===================== 多账号 =====================
def load_accounts():
    """解析账号: 格式 user:pass,user:pass 或 JSON"""
    accounts = []
    if not ACCOUNTS_ENV:
        return accounts

    # 尝试 JSON 格式
    try:
        users = json.loads(ACCOUNTS_ENV)
        if isinstance(users, list):
            for u in users:
                accounts.append({
                    'user': u.get('email', u.get('username', u.get('user', ''))),
                    'pass': u.get('password', u.get('pass', ''))
                })
            return accounts
    except:
        pass

    # user:pass,user:pass 格式
    for a in re.split(r'[,;\n]', ACCOUNTS_ENV):
        a = a.strip()
        if ':' in a:
            u, p = a.split(':', 1)
            accounts.append({'user': u.strip(), 'pass': p.strip()})

    return accounts


def main():
    logger.info("=" * 50)
    logger.info("🚀 KataBump 自动续期启动！")
    logger.info("=" * 50)

    accounts = load_accounts()
    if not accounts:
        logger.error("❌ 未配置账号")
        send_tg("❌ KataBump 续期失败\n未配置账号")
        sys.exit(1)

    logger.info(f"📋 共 {len(accounts)} 个账号")
    results = []
    success_count = 0

    for i, acc in enumerate(accounts):
        logger.info(f"\n{'='*30}\n📋 第 {i+1}/{len(accounts)} 个账号")
        bot = KataBumpRenew(acc['user'], acc['pass'])
        success, msg = bot.run()
        results.append({'msg': msg, 'ok': success,
                        'screenshot': getattr(bot, 'screenshot_path', None)})
        if success:
            success_count += 1

        if bot.driver:
            try:
                bot.driver.quit()
            except:
                pass
            bot.driver = None

        if i < len(accounts) - 1:
            wait = 10000 + random.random() * 5000
            logger.info(f"⏳ 等待 {wait/1000:.0f}s...")
            sleep_ms(wait)

    # 汇总（失败时附带截图发 TG）
    summary = f"📊 续期汇总: {success_count}/{len(accounts)} 成功\n\n"
    summary += "\n\n".join([r['msg'] for r in results])
    logger.info(summary)
    screenshot = next((r['screenshot'] for r in results
                       if r.get('screenshot') and os.path.exists(r['screenshot'])), None)
    if screenshot:
        send_tg(summary, photo_path=screenshot)
    else:
        send_tg(summary)

    sys.exit(0 if success_count == len(accounts) else 1)


if __name__ == "__main__":
    main()
