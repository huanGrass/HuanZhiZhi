import sys
from PySide6 import QtCore, QtWidgets
from huanzhizhi.application import HuanZhiZhiApplication


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName('HuanZhiZhi')
    app.setApplicationDisplayName('幻之之')
    app.setQuitOnLastWindowClosed(False)
    lock = QtCore.QSharedMemory('huanzhizhi.desktop_pet')
    if not lock.create(1):
        if lock.error() == QtCore.QSharedMemory.SharedMemoryError.AlreadyExists:
            return 0
        raise RuntimeError(f'无法创建单实例锁：{lock.errorString()}')
    host = None
    try:
        host = HuanZhiZhiApplication(app)
        host.start()
        exit_code = app.exec()
    except Exception as error:
        QtWidgets.QMessageBox.critical(None, '幻之之启动失败', str(error))
        return 1
    finally:
        if host is not None:
            host._close_components()
        lock.detach()
    if host is not None and host.restart_requested:
        arguments = [] if getattr(sys, 'frozen', False) else ['-m', 'huanzhizhi']
        started, _pid = QtCore.QProcess.startDetached(sys.executable, arguments)
        if not started:
            return 1
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
