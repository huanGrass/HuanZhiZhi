"""Recent floating messages and an on-demand history beside the pet."""
import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from huanzhizhi.widgets import SCROLLBAR_STYLE, TOOL_BUTTON_STYLE
from PySide6.QtSvg import QSvgRenderer

from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow
from huanzhizhi.platform.window_policy import WindowPolicy


class _DockButton(QtWidgets.QToolButton):
    """Match the reference's 16px SVG + 5px gap without native style offsets."""
    def __init__(self, icon: str, text: str = '', *, primary: bool = False):
        super().__init__()
        self.primary = primary
        self.setText(text)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._svg = (Path(__file__).parents[1] / 'assets/dock' / (icon + '.svg')).read_bytes()
        self._renderers = {
            color: QSvgRenderer(self._svg.replace(b'currentColor', color.encode()), self)
            for color in ('#857b92', '#8057b3', '#ffffff')
        }

    def sizeHint(self):
        if self.primary:
            return QtCore.QSize(34, 34)
        width = math.ceil(QtGui.QFontMetricsF(self.font()).horizontalAdvance(self.text())) + 35 if self.text() else 30
        return QtCore.QSize(width, 30)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        hover = self.underMouse() and self.isEnabled()
        color = '#857b92'
        if self.primary:
            color = '#ffffff' if self.isEnabled() else '#857b92'
            background = '#8a5ec1' if hover else '#8057b3'
            if not self.isEnabled():
                background = '#f5effc'
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(background))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()), 11, 11)
        elif hover or self.isDown():
            color = '#8057b3'
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor('#f5effc'))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()), 8, 8)
        icon_x = 7 if self.text() else (self.width()-16)/2
        icon_rect = QtCore.QRectF(icon_x, (self.height()-16)/2, 16, 16)
        if self.text() == '停止':
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(color))
            painter.drawRoundedRect(icon_rect.adjusted(2, 2, -2, -2), 1.33, 1.33)
        else:
            self._renderers[color].render(painter, icon_rect)
        if self.text():
            painter.setPen(QtGui.QColor(color))
            painter.drawText(QtCore.QRectF(28, 0, self.width()-35, self.height()),
                             QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                             self.text())
        if self.hasFocus():
            painter.setPen(QtGui.QPen(QtGui.QColor('#b395d3'), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)


class _MessageBubble(SpeechBubbleWindow):
    def __init__(self):
        super().__init__(0.24)
        self.user_message = False
        self.message_index = None
        self.metadata = QtWidgets.QLabel(self)
        self.metadata.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.metadata.setStyleSheet('color: #96899f; font: 11px "Microsoft YaHei UI";')
        self._layout.insertWidget(0, self.metadata)
        self._layout.setSpacing(3)
        self.label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.label.setStyleSheet('color: #393044; font: 13px "Microsoft YaHei UI";')

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor('#e5dceb'), 1))
        painter.setBrush(QtGui.QColor('#eee5ff' if self.user_message else '#fffefd'))
        rect = self._bubble_rect()
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, 15, 15)
        tail = QtGui.QPainterPath()
        tail.addPolygon(self._tail_polygon(rect))
        painter.drawPath(path.united(tail))


class ExplorationConversation(QtWidgets.QWidget):
    submitted = QtCore.Signal(str)
    stop_requested = QtCore.Signal()
    dismissed = QtCore.Signal()
    tasks_requested = QtCore.Signal()

    def __init__(self):
        flags = QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setWindowTitle('之之探索对话')
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # The visible card is 334px. The surrounding 18px only holds its shadow.
        self.setFixedWidth(370)
        self._policy = WindowPolicy(self)
        self.entries: list[dict[str, str]] = []
        self._history_labels = []
        self._pet_rect = None
        self._shown = False
        self.closed_by_user = False
        self._busy = False
        self._editing_skill = ''
        self._composing = False
        self._history_open = False
        self._left_side = None
        self._layout_ready = False
        self._layout_signature = None
        self._motion = QtCore.QVariantAnimation(self)
        self._motion.setDuration(170)
        self._motion.setStartValue(0.0)
        self._motion.setEndValue(1.0)
        self._motion.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._motion.valueChanged.connect(self._advance_layout)
        self._motion_frames = []
        self.bubbles = [_MessageBubble() for _ in range(3)]
        for bubble in self.bubbles:
            self.destroyed.connect(bubble.deleteLater)
        self.setStyleSheet(TOOL_BUTTON_STYLE + SCROLLBAR_STYLE + '''
            QWidget { color: #3c3346; font: 14px "Microsoft YaHei UI"; }
            QFrame#composer { background: #fffdfd; border: 1px solid #e9e0f0; border-radius: 20px; }
            QFrame#historyCard { background: #fffefd; border: 1px solid #d9cbea; border-radius: 16px; }
            QLabel#target { color: #857b92; font-size: 12px; background: transparent; }
            QTextEdit { border: none; background: transparent; padding: 7px 3px; color: #3c3346;
                        placeholder-text-color: #857b92; selection-background-color: #e4d6f5; }
            QScrollArea { border: none; background: transparent; }
        ''')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 13, 18, 23)
        self.composer = QtWidgets.QFrame()
        self.composer.setObjectName('composer')
        self.composer.setFixedSize(334, 155)
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.composer)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 5)
        shadow.setColor(QtGui.QColor(50, 32, 71, 12))
        self.composer.setGraphicsEffect(shadow)
        composer_layout = QtWidgets.QVBoxLayout(self.composer)
        # QFrame adds its 1px border to these CSS-equivalent padding values.
        composer_layout.setContentsMargins(12, 10, 12, 10)
        composer_layout.setSpacing(0)
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(3, 0, 0, 0)
        header.setSpacing(6)
        target_icon = QtWidgets.QLabel()
        target_icon.setFixedSize(16, 16)
        icon = QtGui.QPixmap(32, 32)
        icon.setDevicePixelRatio(2)
        icon.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(icon)
        svg = (Path(__file__).parents[1] / 'assets/dock/app-window.svg').read_bytes()
        QSvgRenderer(svg.replace(b'currentColor', b'#857b92')).render(painter, QtCore.QRectF(0, 0, 16, 16))
        painter.end()
        target_icon.setPixmap(icon)
        header.addWidget(target_icon)
        self.target_label = QtWidgets.QLabel('当前窗口')
        self.target_label.setObjectName('target')
        self.target_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.target_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        header.addWidget(self.target_label, 1)
        self.close_button = _DockButton('minus')
        self.close_button.setAccessibleName('收起探索对话')
        self.close_button.setToolTip('收起对话，保留历史；当前分析可通过停止按钮结束')
        self.close_button.clicked.connect(self.dismiss)
        header.addWidget(self.close_button)
        composer_layout.addLayout(header)
        composer_layout.addSpacing(3)
        self.suggestions = QtWidgets.QWidget()
        self.suggestion_buttons = []
        self._suggestion_details = []
        self._suggestion_layout = QtWidgets.QVBoxLayout(self.suggestions)
        self._suggestion_layout.setContentsMargins(0, 0, 0, 6)
        self._suggestion_layout.setSpacing(5)
        self.suggestions.hide()
        composer_layout.addWidget(self.suggestions)
        self.input = QtWidgets.QTextEdit()
        self.input.setAcceptRichText(False)
        self.input.setFixedHeight(56)
        self.input.document().setDocumentMargin(0)
        palette = self.input.palette()
        palette.setColor(QtGui.QPalette.ColorRole.PlaceholderText, QtGui.QColor('#857b92'))
        self.input.setPalette(palette)
        self.input.setPlaceholderText('说说你想完成的事…')
        self.input.setAccessibleName('目标或补充说明')
        self.input.setTabChangesFocus(True)
        self.input.textChanged.connect(self._update_send_enabled)
        self.input.textChanged.connect(self._set_input_line_height)
        composer_layout.addWidget(self.input)
        self.send_button = _DockButton('arrow-up', primary=True)
        self.send_button.setAccessibleName('发送消息')
        self.send_button.setToolTip('发送（Enter）；Shift+Enter 换行')
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._submit)
        layout.addWidget(self.composer)
        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(3)
        self.history_button = _DockButton('history', '历史')
        self.history_button.clicked.connect(self.toggle_history)
        self.stop_button = _DockButton('arrow-left', '返回')
        self.stop_button.clicked.connect(self._stop_or_tasks)
        self.stop_button.setEnabled(False)
        for button in (self.history_button, self.stop_button):
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            controls.addWidget(button)
        self.model_label = QtWidgets.QLabel()
        self.model_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.model_label.setStyleSheet('color: #96899f; font-size: 11px; background: transparent; border: none;')
        self.model_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.model_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.model_label.setMinimumWidth(0)
        self.model_label.setContentsMargins(4, 0, 4, 0)
        self.model_label.installEventFilter(self)
        controls.addWidget(self.model_label, 1)
        controls.addWidget(self.send_button)
        for button in (self.close_button, self.send_button):
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        # 9.5px in the reference: textarea baseline spacing + 4px top margin.
        composer_layout.addSpacing(10)
        composer_layout.addLayout(controls)

        self.history_window = QtWidgets.QWidget(None, flags)
        self.destroyed.connect(self.history_window.deleteLater)
        self.history_window.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.history_window.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.history_window.setWindowTitle('之之探索历史')
        self.history_window.setStyleSheet(self.styleSheet())
        self._history_policy = WindowPolicy(self.history_window)
        outer = QtWidgets.QVBoxLayout(self.history_window)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QtWidgets.QFrame()
        card.setObjectName('historyCard')
        outer.addWidget(card)
        inner = QtWidgets.QVBoxLayout(card)
        inner.setContentsMargins(14, 10, 14, 14)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel('探索记录'), 1)
        close = QtWidgets.QToolButton()
        close.setText('收起')
        close.clicked.connect(self.toggle_history)
        header.addWidget(close)
        inner.addLayout(header)
        self.history_scroll = QtWidgets.QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contents = QtWidgets.QWidget()
        self.history_rows = QtWidgets.QVBoxLayout(contents)
        self.history_rows.setContentsMargins(0, 0, 0, 0)
        self.history_rows.addStretch()
        self.history_scroll.setWidget(contents)
        inner.addWidget(self.history_scroll)
        self.target_label.installEventFilter(self)
        self.input.installEventFilter(self)
        self._set_input_line_height()

    def add_message(self, role: str, text: str, *, summary: str | None = None) -> int:
        self.entries.append({'role': role, 'text': text, 'time': QtCore.QTime.currentTime().toString('HH:mm')})
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(20 if role == 'user' else 0, 0, 0 if role == 'user' else 20, 0)
        label = QtWidgets.QTextBrowser()
        label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        label.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        label.setWordWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        label.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        label.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        label.document().setDocumentMargin(10)
        label.document().documentLayout().documentSizeChanged.connect(
            lambda size, message=label: message.setFixedHeight(math.ceil(size.height())))
        color = '#eee5ff' if role == 'user' else '#f7f4fb'
        label.setStyleSheet(f'background: {color}; border: none; border-radius: 12px; padding: 0;')
        layout.addWidget(label)
        self._history_labels.append(label)
        self.history_rows.insertWidget(self.history_rows.count()-1, row)
        self.update_message(len(self.entries)-1, text, summary=summary)
        return len(self.entries)-1

    def update_message(self, index: int, text: str, *, summary: str | None = None) -> None:
        entry = self.entries[index]
        entry['text'] = text
        source = (summary if summary is not None else text).strip()
        brief = ' '.join(source.split())
        if entry['role'] == 'assistant':
            # The model writes a short first line; preserve it even if it runs long.
            entry['summary'] = ' '.join(source.splitlines()[0].split()) if source else ''
        else:
            entry['summary'] = brief if len(brief) <= 36 else brief[:35]+'…'
        name = '你' if entry['role'] == 'user' else '之之'
        self._history_labels[index].setPlainText(f"{name}  {entry['time']}\n{text}")
        self.history_button.setText(f'历史 {len(self.entries)}')
        self.follow_pet(self._pet_rect)
        QtCore.QTimer.singleShot(0, self, self._scroll_to_latest)

    def _scroll_to_latest(self):
        bar = self.history_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self):
        self.closed_by_user = False
        self.entries.clear()
        for bubble in self.bubbles:
            bubble.message_index = None
        self._history_labels.clear()
        while self.history_rows.count() > 1:
            self.history_rows.takeAt(0).widget().deleteLater()
        self.history_button.setText('历史')
        self._history_open = False
        self.input.clear()
        self.set_suggestions([])
        self.set_visible(False)

    def set_suggestions(self, suggestions):
        while self._suggestion_layout.count():
            button = self._suggestion_layout.takeAt(0).widget()
            button.hide()
            button.deleteLater()
        self.suggestion_buttons = []
        self._suggestion_details = []
        for suggestion in suggestions:
            card = QtWidgets.QFrame()
            card.setObjectName('suggestionCard')
            card.setStyleSheet('QFrame#suggestionCard { background: #f7f2fc;'
                              'border: 1px solid #e3d6f0; border-radius: 9px; }')
            row = QtWidgets.QVBoxLayout(card)
            row.setContentsMargins(9, 6, 9, 6)
            row.setSpacing(3)
            button = QtWidgets.QPushButton()
            text = suggestion['title'] + '\n' + suggestion['brief']
            button.setAccessibleName(text)
            button.setToolTip('选择这个方案')
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet('QPushButton { background: transparent; border: none;'
                                'border-radius: 5px; } QPushButton:hover { background: #eee3fc; }'
                                'QPushButton:focus { border: 1px solid #b395d3; }')
            summary_layout = QtWidgets.QVBoxLayout(button)
            summary_layout.setContentsMargins(0, 0, 0, 0)
            label = QtWidgets.QLabel(text)
            label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setStyleSheet('font-size: 12px; background: transparent;')
            label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            summary_layout.addWidget(label)
            button.setFixedHeight(label.heightForWidth(288))
            button.clicked.connect(lambda checked=False, goal=suggestion['goal']: self._submit_suggestion(goal))
            row.addWidget(button)
            toggle = QtWidgets.QToolButton()
            toggle.setText('展开详情')
            toggle.setAccessibleName(suggestion['title'] + '：展开详情')
            toggle.setCheckable(True)
            toggle.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            toggle.setStyleSheet('QToolButton { color: #8057b3; background: transparent;'
                                'border: none; padding: 2px 0; font-size: 11px; }'
                                'QToolButton:hover { color: #62418a; }')
            row.addWidget(toggle, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
            details = QtWidgets.QLabel(suggestion['goal'])
            details.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            details.setWordWrap(True)
            details.setStyleSheet('font-size: 12px; color: #71657e; background: transparent;')
            details.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            details.setFixedHeight(details.heightForWidth(288))
            details.hide()
            row.addWidget(details)
            self._suggestion_details.append((toggle, details))
            toggle.toggled.connect(lambda checked, control=toggle: self._toggle_suggestion_details(control, checked))
            self._suggestion_layout.addWidget(card)
            self.suggestion_buttons.append(button)
        self._resize_suggestions()
        self.set_busy(self._busy)

    def _toggle_suggestion_details(self, control, checked):
        # Keep only one explanation open so the input remains within reach.
        for toggle, details in self._suggestion_details:
            expanded = toggle is control and checked
            with QtCore.QSignalBlocker(toggle):
                toggle.setChecked(expanded)
            toggle.setText('收起详情' if expanded else '展开详情')
            title = toggle.accessibleName().rsplit('：', 1)[0]
            toggle.setAccessibleName(title + '：' + toggle.text())
            details.setVisible(expanded)
        self._resize_suggestions()
        self.set_busy(self._busy)

    def _resize_suggestions(self):
        height = 6
        for index in range(self._suggestion_layout.count()):
            card = self._suggestion_layout.itemAt(index).widget()
            card.setFixedHeight(card.layout().sizeHint().height())
            height += card.height() + (5 if index else 0)
        self.suggestions.setFixedHeight(height)

    def _submit_suggestion(self, goal):
        if not self._busy:
            extra = self.input.toPlainText().strip()
            self.submitted.emit(goal + ('\n补充要求：' + extra if extra else ''))

    def _suggestion_height(self):
        return self.suggestions.height() if self.suggestion_buttons and not self._busy else 0

    def _composer_height(self):
        return (95 if self._busy else 155) + self._suggestion_height()

    def set_busy(self, busy: bool):
        self._busy = busy
        self.suggestions.setVisible(bool(self.suggestion_buttons) and not busy)
        self.input.setVisible(not busy)
        self.send_button.setVisible(not busy)
        if not self._shown:
            self.composer.setFixedHeight(self._composer_height())
        self.stop_button.setText('停止' if busy else '返回')
        self.stop_button.setToolTip('停止当前探索' if busy else '返回任务列表')
        self.stop_button.setEnabled(True)
        self._update_send_enabled()
        self.input.setPlaceholderText('补充你的想法…' if self.entries else
                                      ('说说你想怎样修改这个技能…' if self._editing_skill else '说说你想完成的事…'))
        self.follow_pet(self._pet_rect)

    def set_editing(self, name: str):
        self._editing_skill = name
        self.set_busy(self._busy)

    def set_target(self, title: str):
        self.target_label.setToolTip(title)
        self.target_label.setText(self.target_label.fontMetrics().elidedText(
            title, QtCore.Qt.TextElideMode.ElideRight, self.target_label.width()))

    def set_model_name(self, name: str):
        self.model_label.setToolTip(name)
        self.model_label.setText(self.model_label.fontMetrics().elidedText(
            name, QtCore.Qt.TextElideMode.ElideRight, self.model_label.contentsRect().width()))

    def _update_send_enabled(self):
        self.send_button.setEnabled(bool(self.input.toPlainText().strip()) and not self._busy)

    def _set_input_line_height(self):
        cursor = self.input.textCursor()
        if cursor.blockFormat().lineHeight() == 21:
            return
        # QTextEdit respects fixed paragraph line heights; plain-text insertion
        # and paste remain enforced. Keep this formatting in the user's undo step.
        cursor.joinPreviousEditBlock()
        cursor.select(QtGui.QTextCursor.SelectionType.Document)
        fmt = QtGui.QTextBlockFormat()
        fmt.setLineHeight(21, QtGui.QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        with QtCore.QSignalBlocker(self.input):
            cursor.mergeBlockFormat(fmt)
        cursor.endEditBlock()

    def eventFilter(self, watched, event):
        if watched is self.model_label and event.type() == QtCore.QEvent.Type.Resize:
            self.set_model_name(self.model_label.toolTip())
        if watched is self.target_label and event.type() == QtCore.QEvent.Type.Resize:
            self.set_target(self.target_label.toolTip() or '当前窗口')
        if watched is self.input:
            if event.type() == QtCore.QEvent.Type.InputMethod:
                self._composing = bool(event.preeditString())
            elif (event.type() == QtCore.QEvent.Type.KeyPress
                  and event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter)
                  and not event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
                  and not self._composing):
                self._submit()
                return True
        return super().eventFilter(watched, event)

    def _stop_or_tasks(self):
        if self._busy:
            self.stop_requested.emit()
        else:
            self.tasks_requested.emit()

    def set_visible(self, visible: bool):
        self._shown = visible and not self.closed_by_user
        if not self._shown:
            self._motion.stop()
            self._layout_ready = False
            self._layout_signature = None
            for bubble in self.bubbles:
                bubble.hide()
                bubble.setWindowOpacity(1.0)
            self.history_window.hide()
            self.composer.setFixedHeight(self._composer_height())
            self.input.setFixedHeight(56)
        self.setVisible(self._shown)
        if self._shown:
            self._policy.set_input_passthrough(False, layered=True, no_activate=False)
        self.follow_pet(self._pet_rect)

    def dismiss(self):
        self.closed_by_user = True
        self.set_visible(False)
        self.dismissed.emit()

    def restore(self):
        self.closed_by_user = False
        self.set_visible(True)

    def closeEvent(self, event):
        self.dismiss()
        event.ignore()

    def toggle_history(self):
        self._history_open = not self._history_open
        self.follow_pet(self._pet_rect)

    def _submit(self):
        text = self.input.toPlainText().strip()
        if text and not self._busy:
            self.submitted.emit(text)

    @staticmethod
    def _within_area(point, size, area):
        return QtCore.QPoint(
            max(area.left(), min(point.x(), area.right()-size.width()+1)),
            max(area.top(), min(point.y(), area.bottom()-size.height()+1)))

    def _advance_layout(self, progress):
        if not self._shown:
            return
        start, end = self._height_frame
        height = round(start+(end-start)*progress)
        self.composer.setFixedHeight(height)
        if not self._busy:
            self.input.setFixedHeight(max(0, min(56, round(56*(height-95-self._suggestion_height())/60))))
        margins = self.layout().contentsMargins()
        self.resize(self.width(), height+margins.top()+margins.bottom())
        for widget, start, end, opacity in self._motion_frames:
            point = QtCore.QPoint(round(start.x()+(end.x()-start.x())*progress),
                                 round(start.y()+(end.y()-start.y())*progress))
            widget.move(self._within_area(point, widget.size(), self._motion_area))
            if opacity is not None:
                widget.setWindowOpacity(opacity+(1.0-opacity)*progress)

    def _transition_to(self, targets, height, area, *, animate):
        signature = (height, area.getRect(), tuple(
            (id(widget), point.x(), point.y(), widget.width(),
             height if widget is self else widget.height()) for widget, point in targets))
        if signature == self._layout_signature and (
                animate or self._motion.state() != QtCore.QAbstractAnimation.State.Running):
            return
        self._motion.stop()
        self._layout_signature = signature
        self._motion_area = QtCore.QRect(area)
        self._height_frame = (self.composer.height() if self._layout_ready else height, height)
        self._motion_frames = []
        if not self._layout_ready:
            margins = self.layout().contentsMargins()
            self.composer.setFixedHeight(height)
            self.resize(self.width(), height+margins.top()+margins.bottom())
        for widget, target in targets:
            appearing = widget is not self and not widget.isVisible()
            start = target if not self._layout_ready or appearing else widget.pos()
            opacity = None
            if widget in self.bubbles:
                opacity = 0.0 if appearing else widget.windowOpacity()
                if appearing:
                    start = target+QtCore.QPoint(0, 6)
                    widget.setWindowOpacity(0.0)
            start = self._within_area(start, widget.size(), area)
            self._motion_frames.append((widget, start, target, opacity))
            widget.move(start)
            if appearing:
                widget.show()
                if widget in self.bubbles:
                    widget._window_policy.set_input_passthrough(True, layered=True)
                else:
                    self._history_policy.set_input_passthrough(False, layered=True, no_activate=False)
        self._layout_ready = True
        self._advance_layout(0.0 if animate else 1.0)
        if animate:
            self._motion.start()

    def _recent_bubbles(self):
        indices = list(range(max(0, len(self.entries)-3), len(self.entries)))
        available = [bubble for bubble in self.bubbles if bubble.message_index not in indices]
        recent = []
        for index in indices:
            bubble = next((b for b in self.bubbles if b.message_index == index), None)
            if bubble is None:
                bubble = available.pop(0)
                bubble.hide()
                bubble.message_index = index
            recent.append(bubble)
        # Retained messages keep their native windows; only the expired slot is recycled.
        self.bubbles = recent+available
        return recent

    def follow_pet(self, pet_rect):
        pet_moved = (pet_rect is not None and self._pet_rect is not None
                     and QtCore.QRect(pet_rect) != self._pet_rect)
        if pet_rect is not None:
            self._pet_rect = QtCore.QRect(pet_rect)
        if self._pet_rect is None:
            return
        if not self._shown:
            for bubble in self.bubbles:
                bubble.hide()
            self.history_window.hide()
            return
        pet = self._pet_rect
        area = (QtGui.QGuiApplication.screenAt(pet.center()) or self.screen()).availableGeometry()
        margins = self.layout().contentsMargins()
        left_x = pet.left()-self.composer.width()-12-margins.left()
        # Keep the current side around the threshold instead of flipping every pixel.
        if self._left_side is None:
            self._left_side = left_x >= area.left()
        elif self._left_side and left_x < area.left()-12:
            self._left_side = False
        elif not self._left_side and left_x >= area.left()+12:
            self._left_side = True
        left_side = self._left_side
        x = left_x if left_side else pet.right()+13-margins.left()
        x = min(max(x, area.left()), area.right()-self.width()+1)
        target_height = self._composer_height()
        window_height = target_height+margins.top()+margins.bottom()
        bottom = min(max(pet.bottom()+margins.bottom(), area.top()+window_height), area.bottom())
        recent = self._recent_bubbles()
        for bubble in recent:
            entry = self.entries[bubble.message_index]
            bubble.user_message = entry['role'] == 'user'
            bubble._set_tail_edge('right' if left_side else 'left')
            name = '你' if bubble.user_message else '之之'
            metadata = f'{name} · {entry["time"]}'
            text = entry['summary']
            bubble.metadata.setText(metadata)
            bubble.label.setText(text)
            bubble._fit_label_width(text+'\n'+metadata)
            bubble.metadata.setFixedWidth(bubble.label.width())
            # Unusually long first lines remain complete in the history view.
            bubble.label.setMaximumHeight(max(20, area.height()-window_height-80))
            bubble.label.adjustSize()
            bubble.adjustSize()
            bubble.update()
        shown = list(recent)
        total = sum(b.height()+8 for b in shown)
        needed = min(420, area.height()-window_height-20) if self._history_open else total
        bottom = min(max(bottom, area.top()+needed+window_height+16), area.bottom())
        targets = [(self, QtCore.QPoint(x, bottom-window_height+1))]
        card_x = x+margins.left()
        card_y = bottom-window_height+1+margins.top()
        while len(shown) > 1 and total > card_y-area.top()-8:
            total -= shown.pop(0).height()+8
        y = max(area.top(), card_y-total-8)
        for bubble in shown:
            bubble_x = card_x+(16 if bubble.user_message else 0)
            targets.append((bubble, self._within_area(QtCore.QPoint(bubble_x, y), bubble.size(), area)))
            y += bubble.height()+8
        if self._history_open:
            self.history_window.resize(320, min(420, area.height()-window_height-20))
            history_x = card_x-self.history_window.width()-12
            if history_x < area.left():
                history_x = card_x
                shown = []
            else:
                shown = shown[-1:]
            targets.append((self.history_window, self._within_area(
                QtCore.QPoint(history_x, card_y-self.history_window.height()-8),
                self.history_window.size(), area)))
        else:
            self.history_window.hide()
        for bubble in self.bubbles:
            if bubble not in shown:
                bubble.hide()
        targets = [(widget, point) for widget, point in targets
                   if widget not in self.bubbles or widget in shown]
        # Anchor movement follows the pointer immediately, including when it
        # interrupts a message/height transition. Unchanged anchors keep those animations.
        self._transition_to(targets, target_height, area, animate=not pet_moved)
