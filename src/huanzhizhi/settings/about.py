"""About page and an explicit, read-only release check."""
import json
from pathlib import Path
import re
import tomllib

from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets

PROJECT_URL = "https://github.com/huanGrass/HuanZhiZhi"
RELEASES_URL = PROJECT_URL + "/releases"
RELEASES_API = "https://api.github.com/repos/huanGrass/HuanZhiZhi/releases?per_page=100"
QQ_GROUP = "1064560882"
MAX_RESPONSE = 1024 * 1024


def version_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError("版本号格式无法识别")
    return tuple(map(int, value.removeprefix("v").split(".")))


def current_version():
    root = Path(__file__).resolve().parents[3]
    try:
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        version_number(version)
        return version
    except (OSError, ValueError, KeyError, TypeError):
        return "未知"


def latest_release(data):
    if not isinstance(data, list):
        raise ValueError("发行版信息格式无效")
    releases = []
    for release in data:
        if not isinstance(release, dict) or not isinstance(release.get("draft"), bool) or not isinstance(release.get("prerelease"), bool):
            raise ValueError("发行版信息不完整")
        if release["draft"] or release["prerelease"]:
            continue
        number = version_number(release.get("tag_name"))
        if not isinstance(release.get("body"), (str, type(None))):
            raise ValueError("更新说明格式无效")
        releases.append((number, release))
    if not releases:
        # Do not claim there are no stable releases when more pages may exist.
        if len(data) >= 100:
            raise ValueError("发行版列表较长，请打开下载页查看正式版本")
        return None
    return max(releases, key=lambda entry: entry[0])[1]


class AboutPage(QtWidgets.QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.version = current_version()
        self._reply = None
        self._data = bytearray()
        self._failure = ""
        self._download_url = RELEASES_URL
        self.network = QtNetwork.QNetworkAccessManager(self)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(20000)
        self.timer.timeout.connect(self._timeout)

        page = QtWidgets.QWidget()
        page.setObjectName("desktopPetSettingsContent")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QtWidgets.QLabel("关于幻之之")
        title.setObjectName("desktopPetSettingsTitle")
        layout.addWidget(title)

        version_card = QtWidgets.QFrame()
        version_card.setObjectName("desktopPetSettingsCard")
        version_layout = QtWidgets.QVBoxLayout(version_card)
        version_layout.setContentsMargins(18, 16, 18, 16)
        version_layout.setSpacing(10)
        self.version_label = QtWidgets.QLabel("幻之之 · 当前版本 " + self.version)
        self.version_label.setObjectName("desktopPetSettingsRowTitle")
        version_layout.addWidget(self.version_label)
        controls = QtWidgets.QHBoxLayout()
        self.check_button = QtWidgets.QPushButton("检查更新")
        self.download_button = QtWidgets.QPushButton("打开下载页")
        controls.addWidget(self.check_button)
        controls.addWidget(self.download_button)
        controls.addStretch()
        version_layout.addLayout(controls)
        self.status = QtWidgets.QLabel("点击检查正式版本更新。")
        self.status.setObjectName("desktopPetSettingsRowDetail")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        version_layout.addWidget(self.status)
        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName("新版本更新说明")
        self.notes.setMinimumHeight(100)
        self.notes.setMaximumHeight(180)
        self.notes.hide()
        version_layout.addWidget(self.notes)
        layout.addWidget(version_card)

        idea = QtWidgets.QFrame()
        idea.setObjectName("desktopPetSettingsCard")
        idea_layout = QtWidgets.QVBoxLayout(idea)
        idea_layout.setContentsMargins(18, 16, 18, 16)
        slogan = QtWidgets.QLabel("让 AI 帮你做事，也把做事的方法留下来。")
        slogan.setObjectName("desktopPetSettingsRowTitle")
        slogan.setWordWrap(True)
        idea_layout.addWidget(slogan)
        description = QtWidgets.QLabel(
            "幻之之是一只 Windows 桌宠，昵称「之之」。\n\n"
            "用自然语言描述需求，让 AI 把完成任务的方法写成技能。"
            "试用、调整后留下来，下次直接运行；也欢迎分享自己的技能，让之之一起成长。")
        description.setObjectName("desktopPetSettingsRowDetail")
        description.setWordWrap(True)
        idea_layout.addWidget(description)
        layout.addWidget(idea)

        community = QtWidgets.QFrame()
        community.setObjectName("desktopPetSettingsCard")
        community_layout = QtWidgets.QVBoxLayout(community)
        community_layout.setContentsMargins(18, 16, 18, 16)
        group_row = QtWidgets.QHBoxLayout()
        group = QtWidgets.QLabel("交流 QQ 群：" + QQ_GROUP)
        group.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_button = QtWidgets.QPushButton("复制群号")
        group_row.addWidget(group)
        group_row.addStretch()
        group_row.addWidget(self.copy_button)
        community_layout.addLayout(group_row)
        hint = QtWidgets.QLabel("交流想法、分享技能或反馈问题。下载失败时，可到 QQ 群查看群文件中的安装包和组件。")
        hint.setObjectName("desktopPetSettingsRowDetail")
        hint.setWordWrap(True)
        community_layout.addWidget(hint)
        links = QtWidgets.QHBoxLayout()
        self.project_button = QtWidgets.QPushButton("项目地址")
        self.project_button.setToolTip(PROJECT_URL)
        self.project_button.clicked.connect(lambda: self._open_url(PROJECT_URL))
        links.addWidget(self.project_button)
        self.log_button = QtWidgets.QPushButton("查看运行日志")
        links.addWidget(self.log_button)
        links.addStretch()
        community_layout.addLayout(links)
        layout.addWidget(community)
        layout.addStretch()
        self.setWidget(page)
        self.check_button.clicked.connect(self.check_updates)
        self.download_button.clicked.connect(lambda: self._open_url(self._download_url))
        self.copy_button.clicked.connect(self.copy_group)

    def _open_url(self, url):
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)):
            self.status.setText("无法打开浏览器，请重试或到 QQ 群获取帮助。")

    def copy_group(self):
        QtWidgets.QApplication.clipboard().setText(QQ_GROUP)
        self.copy_button.setText("已复制")

    def check_updates(self):
        if self._reply is not None:
            return
        self.notes.hide()
        self._download_url = RELEASES_URL
        self.download_button.setText("打开下载页")
        self._data.clear()
        self._failure = ""
        if self.version == "未知":
            self.status.setText("无法读取当前版本，请打开下载页核对版本。")
            return
        self.status.setText("正在检查更新…")
        self.check_button.setEnabled(False)
        url = QtCore.QUrl(RELEASES_API)
        proxies = QtNetwork.QNetworkProxyFactory.systemProxyForQuery(QtNetwork.QNetworkProxyQuery(url))
        if proxies:
            self.network.setProxy(proxies[0])
        request = QtNetwork.QNetworkRequest(url)
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", b"HuanZhiZhi-update-check")
        request.setTransferTimeout(15000)
        reply = self.network.get(request)
        self._reply = reply
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))
        self.timer.start()

    def _read(self, reply):
        if reply is not self._reply:
            return
        self._data.extend(bytes(reply.readAll()))
        if len(self._data) > MAX_RESPONSE:
            self._failure = "服务器响应过大"
            reply.abort()

    def _timeout(self):
        if self._reply is not None:
            self._failure = "连接超时"
            self._reply.abort()

    def _finished(self, reply):
        if reply is not self._reply:
            reply.deleteLater()
            return
        self.timer.stop()
        self._reply = None
        self.check_button.setEnabled(True)
        try:
            status = reply.attribute(QtNetwork.QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if self._failure:
                raise ValueError(self._failure)
            if status in (403, 429):
                raise ValueError("服务暂时限制访问，请稍后重试")
            if reply.error() != QtNetwork.QNetworkReply.NetworkError.NoError or status != 200:
                raise ValueError("网络不可用或服务器未正常响应")
            release = latest_release(json.loads(self._data))
            if release is None:
                self.status.setText("暂无正式发布版本。可到 QQ 群了解开发进展。")
                return
            latest = version_number(release["tag_name"])
            current = version_number(self.version)
            if latest > current:
                self.status.setText("发现新版本 " + release["tag_name"] + "，可查看说明并前往下载。")
                self._download_url = RELEASES_URL + "/tag/" + release["tag_name"]
                self.download_button.setText("下载新版本")
                self.notes.setPlainText(release.get("body") or "此版本暂无更新说明。")
                self.notes.show()
            elif latest == current:
                self.status.setText("已是最新正式版本。")
            else:
                self.status.setText("当前版本比最新正式发行版更新，暂无需要下载的更新。")
        except (ValueError, TypeError, KeyError) as error:
            self.status.setText("检查更新失败：" + str(error) + "。仍可打开下载页或到 QQ 群查看。")
        finally:
            reply.deleteLater()

    def stop(self):
        self.timer.stop()
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            self.status.setText("已取消更新检查。")
        self.check_button.setEnabled(True)
