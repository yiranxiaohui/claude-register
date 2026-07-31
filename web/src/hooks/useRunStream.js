import { useCallback, useEffect, useRef, useState } from "react";

// 全局唯一的注册运行日志流：App 持有，注册页展示，账号页触发重跑后 attach。
export function useRunStream() {
  const [activeRunId, setActiveRunId] = useState(null);
  const [activeStatus, setActiveStatus] = useState(null);
  const [logLines, setLogLines] = useState([]);
  const [streamEpoch, setStreamEpoch] = useState(0);
  const esRef = useRef(null);

  useEffect(() => {
    return () => {
      if (esRef.current) esRef.current.close();
    };
  }, []);

  const attach = useCallback((runId) => {
    if (esRef.current) esRef.current.close();
    setLogLines([]);
    setActiveRunId(runId);
    setActiveStatus("running");
    const es = new EventSource(`/api/runs/${runId}/stream`);
    esRef.current = es;
    es.addEventListener("log", (e) => {
      setLogLines((prev) => [...prev, e.data]);
    });
    es.addEventListener("done", (e) => {
      setActiveStatus(e.data || "success");
      es.close();
      esRef.current = null;
      setStreamEpoch((n) => n + 1);
    });
    es.onerror = () => {
      es.close();
      esRef.current = null;
    };
  }, []);

  return { activeRunId, activeStatus, logLines, attach, streamEpoch };
}
