"""后台单任务执行：全局锁 + 线程 + sink 捕获日志落库。"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from claude_register import browser
from claude_register import console
from claude_register import flow as flow_module
from server import db


class RunnerBusy(Exception):
    pass


class Runner:
    def __init__(self, conn, data_dir: Path, now_fn):
        self.conn = conn
        self.data_dir = Path(data_dir)
        self.now_fn = now_fn
        self._lock = threading.Lock()
        self._active_id: int | None = None
        self._queues: dict[int, queue.Queue] = {}

    def is_busy(self) -> bool:
        with self._lock:
            return self._active_id is not None

    def subscribe(self, run_id):
        with self._lock:
            if run_id != self._active_id:
                return None
        return self._queues.get(run_id)

    def start(self, config, *, email=None, domain=None, flow_fn=None):
        flow_fn = flow_fn or flow_module.run
        with self._lock:
            if self._active_id is not None:
                raise RunnerBusy("已有注册任务在运行")
            run_dir = self.data_dir / "runs"
            rid = db.create_run(
                self.conn, email or "", domain or config.anymail_domain,
                "", self.now_fn(),
            )
            out_dir = run_dir / str(rid)
            out_dir.mkdir(parents=True, exist_ok=True)
            self.conn.execute("UPDATE runs SET output_dir=? WHERE id=?",
                              (str(out_dir), rid))
            self.conn.commit()
            q: queue.Queue = queue.Queue()
            self._queues[rid] = q
            self._active_id = rid
        t = threading.Thread(target=self._run, args=(rid, out_dir, config, email, domain, flow_fn, q),
                             daemon=True)
        t.start()
        return rid

    def _run(self, rid, out_dir: Path, config, email, domain, flow_fn, q):
        log_path = out_dir / "log.txt"
        fh = log_path.open("a", encoding="utf-8")

        def sink(msg: str):
            fh.write(msg + "\n")
            fh.flush()
            q.put({"type": "log", "line": msg})

        token = console.set_sink(sink)
        token2 = browser.set_output_dir(out_dir)
        status = "success"
        try:
            flow_fn(email=email, domain=domain, config=config)
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            sink(f"运行出错：{exc!r}")
        finally:
            console.reset_sink(token)
            browser._output_dir.reset(token2)
            db.finish_run(self.conn, rid, status, self.now_fn())
            q.put({"type": "done", "status": status})
            fh.close()
            with self._lock:
                self._active_id = None
                self._queues.pop(rid, None)  # 清理已完成 run 的队列句柄，避免无限累积
