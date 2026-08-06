from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QCoreApplication,
    QIODevice,
    QLockFile,
    QObject,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QStackedLayout,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .config import RuntimeConfig
from .native_dashboard import DesktopBackend, NativeDashboard


def _finish(
    future: concurrent.futures.Future[Any],
    *,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)


class AccountPage(QWebEnginePage):
    def __init__(
        self,
        profile: QWebEngineProfile,
        bridge: QtBrowserBridge,
        account_id: str,
        parent: QObject,
    ) -> None:
        super().__init__(profile, parent)
        self.bridge = bridge
        self.account_id = account_id

    def createWindow(self, window_type: QWebEnginePage.WebWindowType) -> QWebEnginePage:
        del window_type
        return self.bridge.create_popup_page(self.account_id, self.profile())


class QtBrowserBridge(QObject):
    open_requested = Signal(str, str, str, object)
    close_requested = Signal(str, object)
    focus_requested = Signal(str, object)
    activate_requested = Signal(str, object)
    navigate_requested = Signal(str, str, object)
    javascript_requested = Signal(str, str, object)
    state_requested = Signal(str, object)
    cookies_requested = Signal(str, object)
    set_cookies_requested = Signal(str, object, object)
    screenshot_requested = Signal(str, object)
    quit_requested = Signal()
    window_activation_requested = Signal()

    def __init__(self, window: DesktopWindow) -> None:
        super().__init__(window)
        self.window = window
        self.sessions: dict[str, dict[str, Any]] = {}
        self.popup_views: list[QWebEngineView] = []
        self.background_open_accounts: set[str] = set()
        self.open_requested.connect(self._open_account)
        self.close_requested.connect(self._close_account)
        self.focus_requested.connect(self._focus_account)
        self.activate_requested.connect(self._activate_account)
        self.navigate_requested.connect(self._navigate)
        self.javascript_requested.connect(self._run_javascript)
        self.state_requested.connect(self._state)
        self.cookies_requested.connect(self._cookies)
        self.set_cookies_requested.connect(self._set_cookies)
        self.screenshot_requested.connect(self._screenshot)
        self.quit_requested.connect(QApplication.instance().quit)
        self.window_activation_requested.connect(self.window.activate_window)

    def mark_background_open(self, account_ids: list[str]) -> None:
        self.background_open_accounts.update(account_ids)

    async def _request(
        self,
        signal: Signal,
        *args: Any,
        timeout: float = 15,
        operation: str = "浏览器操作",
    ) -> Any:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        signal.emit(*args, future)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            future.cancel()
            raise RuntimeError(f"{operation}超过 {int(timeout)} 秒未响应") from exc

    async def open_account(self, account_id: str, data_dir: Path, url: str) -> None:
        await self._request(
            self.open_requested,
            account_id,
            str(data_dir),
            url,
            timeout=70,
            operation="打开账号页面",
        )

    async def close_account(self, account_id: str) -> None:
        await self._request(self.close_requested, account_id, operation="关闭账号页面")

    async def focus_account(self, account_id: str) -> None:
        await self._request(self.focus_requested, account_id, timeout=8, operation="切换账号页面")

    async def activate_account(self, account_id: str) -> None:
        await self._request(
            self.activate_requested,
            account_id,
            timeout=8,
            operation="激活账号页面",
        )

    async def navigate(self, account_id: str, url: str) -> None:
        await self._request(
            self.navigate_requested,
            account_id,
            url,
            timeout=70,
            operation="加载账号页面",
        )

    async def run_javascript(self, account_id: str, script: str) -> Any:
        return await self._request(
            self.javascript_requested,
            account_id,
            script,
            timeout=12,
            operation="读取豆包页面",
        )

    async def state(self, account_id: str) -> dict[str, Any]:
        return await self._request(
            self.state_requested,
            account_id,
            timeout=8,
            operation="读取页面状态",
        )

    async def cookies(self, account_id: str) -> list[dict[str, Any]]:
        return await self._request(
            self.cookies_requested,
            account_id,
            timeout=8,
            operation="读取登录状态",
        )

    async def set_cookies(
        self,
        account_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        await self._request(
            self.set_cookies_requested,
            account_id,
            records,
            operation="写入登录状态",
        )

    async def screenshot(self, account_id: str) -> bytes:
        return await self._request(
            self.screenshot_requested,
            account_id,
            operation="截取账号页面",
        )

    def _session(
        self,
        account_id: str,
        future: concurrent.futures.Future[Any],
    ) -> dict[str, Any] | None:
        session = self.sessions.get(account_id)
        if session is None:
            _finish(future, error=RuntimeError("账号内置浏览器标签页不存在"))
        return session

    @staticmethod
    def _keep_page_active(page: QWebEnginePage) -> None:
        page.setVisible(True)
        page.setLifecycleState(QWebEnginePage.LifecycleState.Active)

    @Slot(str, str, str, object)
    def _open_account(
        self,
        account_id: str,
        data_dir: str,
        url: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        open_in_background = account_id in self.background_open_accounts
        self.background_open_accounts.discard(account_id)
        existing = self.sessions.get(account_id)
        if existing is not None:
            if open_in_background:
                self.window.tabs.activateForAutomation(existing["view"])
            else:
                self.window.tabs.setCurrentWidget(existing["view"])
                self.window.activate_window()
            _finish(future)
            return
        try:
            digest = hashlib.sha1(account_id.encode("utf-8")).hexdigest()[:12]
            profile = QWebEngineProfile(f"doubao-{digest}", self)
            root = Path(data_dir)
            storage = root / "embedded-storage"
            cache = root / "embedded-cache"
            storage.mkdir(parents=True, exist_ok=True)
            cache.mkdir(parents=True, exist_ok=True)
            profile.setPersistentStoragePath(str(storage))
            profile.setCachePath(str(cache))
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )
            settings = profile.settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalStorageEnabled,
                True,
            )
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled,
                True,
            )

            view = QWebEngineView(self.window.tabs)
            page = AccountPage(profile, self, account_id, view)
            view.setPage(page)
            self._keep_page_active(page)
            page.recommendedStateChanged.connect(
                lambda _, value=page: self._keep_page_active(value)
            )
            page.visibleChanged.connect(lambda _, value=page: self._keep_page_active(value))
            cookie_map: dict[tuple[str, str, str], dict[str, Any]] = {}
            session = {
                "profile": profile,
                "page": page,
                "view": view,
                "cookies": cookie_map,
                "loading": True,
                "load_finished": False,
                "load_success": None,
            }
            self.sessions[account_id] = session
            view.loadStarted.connect(
                lambda value=session: value.update(
                    loading=True,
                    load_finished=False,
                    load_success=None,
                )
            )
            view.loadFinished.connect(
                lambda success, value=session: value.update(
                    loading=False,
                    load_finished=True,
                    load_success=bool(success),
                )
            )
            cookie_store = profile.cookieStore()
            cookie_store.cookieAdded.connect(
                lambda cookie, value=account_id: self._cookie_added(value, cookie)
            )
            cookie_store.cookieRemoved.connect(
                lambda cookie, value=account_id: self._cookie_removed(value, cookie)
            )
            cookie_store.loadAllCookies()

            index = self.window.tabs.addTab(view, f"账号 · {account_id}")
            if open_in_background:
                session["background_restore"] = self.window.tabs.activateForAutomation(
                    view,
                    restore_delay_ms=8_000,
                )
            else:
                self.window.tabs.setCurrentIndex(index)
            view.titleChanged.connect(
                lambda title, value=view, aid=account_id: self._update_title(
                    value,
                    aid,
                    title,
                )
            )
            view.setUrl(QUrl(url))
            if not open_in_background:
                self.window.activate_window()
            _finish(future)
        except Exception as exc:
            self.sessions.pop(account_id, None)
            _finish(future, error=exc)

    def _update_title(
        self,
        view: QWebEngineView,
        account_id: str,
        title: str,
    ) -> None:
        session = self.sessions.get(account_id)
        if session is not None and title.strip():
            # Doubao's long-lived SPA does not consistently emit loadFinished.
            # A real document title is the reliable signal that the page shell
            # has loaded far enough for DOM readiness checks.
            session.update(
                loading=False,
                load_finished=True,
                load_success=True,
            )
            restore = session.pop("background_restore", None)
            if callable(restore):
                restore()
        index = self.window.tabs.indexOf(view)
        if index >= 0:
            short = title.strip()[:24] if title.strip() else "豆包"
            self.window.tabs.setTabText(index, f"{account_id} · {short}")

    def _cookie_record(self, cookie: QNetworkCookie) -> dict[str, Any]:
        return {
            "name": bytes(cookie.name()).decode("utf-8", "replace"),
            "value": bytes(cookie.value()).decode("utf-8", "replace"),
            "domain": cookie.domain(),
            "path": cookie.path(),
            "secure": cookie.isSecure(),
            "httpOnly": cookie.isHttpOnly(),
        }

    def _cookie_added(self, account_id: str, cookie: QNetworkCookie) -> None:
        session = self.sessions.get(account_id)
        if session is None:
            return
        item = self._cookie_record(cookie)
        key = (item["domain"], item["path"], item["name"])
        session["cookies"][key] = item

    def _cookie_removed(self, account_id: str, cookie: QNetworkCookie) -> None:
        session = self.sessions.get(account_id)
        if session is None:
            return
        item = self._cookie_record(cookie)
        session["cookies"].pop((item["domain"], item["path"], item["name"]), None)

    @Slot(str, object)
    def _close_account(
        self,
        account_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self.sessions.pop(account_id, None)
        if session is not None:
            view: QWebEngineView = session["view"]
            index = self.window.tabs.indexOf(view)
            if index >= 0:
                self.window.tabs.removeTab(index)
            view.setUrl(QUrl("about:blank"))
            view.deleteLater()
            session["page"].deleteLater()
            session["profile"].deleteLater()
        _finish(future)

    @Slot(str, object)
    def _focus_account(
        self,
        account_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is not None:
            view: QWebEngineView = session["view"]
            self._keep_page_active(session["page"])
            self.window.tabs.setCurrentWidget(view)
            view.setFocus(Qt.FocusReason.OtherFocusReason)
            _finish(future)

    @Slot(str, object)
    def _activate_account(
        self,
        account_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is not None:
            self._keep_page_active(session["page"])
            view: QWebEngineView = session["view"]
            self.window.tabs.activateForAutomation(view)
            _finish(future)

    @Slot(str, str, object)
    def _navigate(
        self,
        account_id: str,
        url: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is None:
            return
        view: QWebEngineView = session["view"]
        completed = False

        def loaded(_: bool) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            with contextlib.suppress(RuntimeError):
                view.loadFinished.disconnect(loaded)
            _finish(future)

        view.loadFinished.connect(loaded)
        view.setUrl(QUrl(url))
        QTimer.singleShot(60_000, lambda: _finish(future))

    @Slot(str, str, object)
    def _run_javascript(
        self,
        account_id: str,
        script: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is None:
            return
        try:
            self._keep_page_active(session["page"])
            self.window.tabs.activateForAutomation(session["view"])
            session["page"].runJavaScript(
                script,
                lambda result: _finish(future, result=result),
            )
            QTimer.singleShot(
                6_000,
                lambda: _finish(
                    future,
                    error=RuntimeError("豆包页面脚本执行超时"),
                ),
            )
        except Exception as exc:
            _finish(future, error=exc)

    @Slot(str, object)
    def _state(
        self,
        account_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is not None:
            _finish(
                future,
                result={
                    "page_url": session["view"].url().toString(),
                    "page_title": session["view"].title(),
                    "loading": bool(session.get("loading", False)),
                    "load_finished": bool(session.get("load_finished", False)),
                    "load_success": session.get("load_success"),
                },
            )

    @Slot(str, object)
    def _cookies(
        self,
        account_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is not None:
            _finish(future, result=list(session["cookies"].values()))

    @Slot(str, object, object)
    def _set_cookies(
        self,
        account_id: str,
        records: list[dict[str, Any]],
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is None:
            return
        store = session["profile"].cookieStore()
        origin = QUrl("https://www.doubao.com/")
        for item in records:
            cookie = QNetworkCookie(
                str(item["name"]).encode(),
                str(item["value"]).encode(),
            )
            cookie.setDomain(str(item.get("domain", ".doubao.com")))
            cookie.setPath(str(item.get("path", "/")))
            cookie.setSecure(bool(item.get("secure", True)))
            store.setCookie(cookie, origin)
        _finish(future)

    @Slot(str, object)
    def _screenshot(
        self,
        account_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        session = self._session(account_id, future)
        if session is None:
            return
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        session["view"].grab().save(buffer, "PNG")
        buffer.close()
        _finish(future, result=bytes(data))

    def create_popup_page(
        self,
        account_id: str,
        profile: QWebEngineProfile,
    ) -> QWebEnginePage:
        view = QWebEngineView(self.window.tabs)
        page = AccountPage(profile, self, account_id, view)
        view.setPage(page)
        self._keep_page_active(page)
        page.recommendedStateChanged.connect(lambda _, value=page: self._keep_page_active(value))
        page.visibleChanged.connect(lambda _, value=page: self._keep_page_active(value))
        self.popup_views.append(view)
        index = self.window.tabs.addTab(view, f"{account_id} · 新页面")
        self.window.tabs.setCurrentIndex(index)
        view.titleChanged.connect(
            lambda title, value=view, aid=account_id: self._update_title(
                value,
                aid,
                title,
            )
        )
        return page


class PersistentTabWidget(QWidget):
    """Tab container that keeps background browser widgets visible and active.

    QTabWidget hides every non-current page. Doubao observes that visibility
    change and may pause answer generation. StackAll keeps account views alive
    underneath the opaque management dashboard while the tab bar still behaves
    like a normal tab switcher.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.bar = QTabBar(self)
        self.bar.setShape(QTabBar.Shape.RoundedNorth)
        self.bar.currentChanged.connect(self._activate)
        layout.addWidget(self.bar)
        host = QWidget(self)
        self.stack = QStackedLayout(host)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        layout.addWidget(host, 1)

    def _activate(self, index: int) -> None:
        if not 0 <= index < self.stack.count():
            return
        self.stack.setCurrentIndex(index)
        current = self.stack.widget(index)
        current.setVisible(True)
        current.raise_()

    def addTab(self, widget: QWidget, label: str) -> int:
        return self.insertTab(self.stack.count(), widget, label)

    def insertTab(self, index: int, widget: QWidget, label: str) -> int:
        index = max(0, min(index, self.stack.count()))
        self.stack.insertWidget(index, widget)
        tab_index = self.bar.insertTab(index, label)
        widget.setVisible(True)
        if self.bar.currentIndex() < 0:
            self.setCurrentIndex(tab_index)
        return tab_index

    def removeTab(self, index: int) -> None:
        widget = self.stack.widget(index)
        if widget is not None:
            self.stack.removeWidget(widget)
            widget.setVisible(False)
        self.bar.removeTab(index)

    def setCurrentIndex(self, index: int) -> None:
        self.bar.setCurrentIndex(index)
        self._activate(index)

    def setCurrentWidget(self, widget: QWidget) -> None:
        index = self.indexOf(widget)
        if index >= 0:
            self.setCurrentIndex(index)

    def currentWidget(self) -> QWidget | None:
        return self.stack.widget(self.bar.currentIndex())

    def indexOf(self, widget: QWidget) -> int:
        return self.stack.indexOf(widget)

    def setTabText(self, index: int, text: str) -> None:
        self.bar.setTabText(index, text)

    def setDocumentMode(self, enabled: bool) -> None:
        self.bar.setDocumentMode(enabled)

    def setMovable(self, enabled: bool) -> None:
        self.bar.setMovable(enabled)

    def setTabsClosable(self, enabled: bool) -> None:
        self.bar.setTabsClosable(enabled)

    def activateForAutomation(
        self,
        widget: QWidget,
        *,
        restore_delay_ms: int = 120,
    ) -> Callable[[], None]:
        """Keep a browser page active for background automation.

        The tab widget uses StackAll so account pages stay alive underneath
        the dashboard. Chromium backgrounding flags are disabled via
        QTWEBENGINE_CHROMIUM_FLAGS, so JavaScript can run without visually
        bringing the page forward.
        """

        del restore_delay_ms
        index = self.indexOf(widget)
        if index < 0:
            return lambda: None
        # Visibility is enough; do not change the current tab or raise the
        # widget, which would cause the management dashboard to flicker.
        widget.setVisible(True)
        return lambda: None


class DesktopWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("豆包关键词资料采集器")
        self.resize(1440, 940)
        self.setMinimumSize(1050, 700)
        self.tabs = PersistentTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tabs.setTabsClosable(False)
        self.setCentralWidget(self.tabs)

    def set_dashboard(self, dashboard: NativeDashboard) -> None:
        self.tabs.insertTab(0, dashboard, "采集管理中心")
        self.tabs.setCurrentIndex(0)

    @Slot()
    def activate_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()


def run_desktop(runtime: RuntimeConfig | None = None) -> None:
    runtime_config = runtime or RuntimeConfig.from_env()
    QCoreApplication.setAttribute(
        Qt.ApplicationAttribute.AA_ShareOpenGLContexts,
        True,
    )
    qt_app = QApplication.instance() or QApplication([])
    qt_app.setApplicationName("豆包关键词资料采集器")
    qt_app.setOrganizationName("DoubaoKeywordCollector")
    lock_path = (
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
        / "doubao-keyword-collector.lock"
    )
    instance_lock = QLockFile(str(lock_path))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(100):
        QMessageBox.information(
            None,
            "软件已在运行",
            "豆包关键词资料采集器已经打开，请切换到现有窗口。",
        )
        return

    window = DesktopWindow()
    bridge = QtBrowserBridge(window)
    backend = DesktopBackend(bridge, runtime_config)
    dashboard = NativeDashboard(backend, window.tabs)
    window.set_dashboard(dashboard)
    window.showMaximized()
    qt_app.exec()

    shutdown_future = backend.shutdown()
    deadline = time.monotonic() + 8
    while not shutdown_future.done() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.05)
    instance_lock.unlock()
