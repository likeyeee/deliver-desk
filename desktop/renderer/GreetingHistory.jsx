import React, { useEffect, useRef, useState } from "react";
import { History, RefreshCw, X } from "lucide-react";

const statuses = {
  generating: "正在生成",
  generated: "已生成",
  failed: "生成失败",
  stopped: "生成已停止",
  sending: "发送中",
  sent: "已送达",
  unknown: "待核对",
  partial: "待核对",
  contacted: "已沟通",
  not_sent: "未发送",
};
const time = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const result = (row) =>
  statuses[row.delivery_status || row.status] || row.status;

function GreetingDetail({ record, close }) {
  const dialog = useRef(null);
  useEffect(() => {
    dialog.current.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal greeting-detail"
      onCancel={close}
      aria-labelledby="greeting-detail-title"
    >
      <button
        className="modal-close icon-button"
        aria-label="关闭招呼记录"
        onClick={close}
      >
        <X size={18} />
      </button>
      <h2 id="greeting-detail-title">{record.title}</h2>
      <p>
        {record.company} · {result(record)}
      </p>
      <dl>
        <dt>发现方式</dt>
        <dd>{record.mode === "preview" ? "预览职位" : "正式投递"}</dd>
        <dt>生成时间</dt>
        <dd>{time(record.created_at)}</dd>
        <dt>使用简历</dt>
        <dd>{record.resume_name}</dd>
        <dt>简历版本</dt>
        <dd title={record.resume_revision}>
          {record.resume_revision.slice(0, 12)}
        </dd>
        <dt>使用模型</dt>
        <dd>{record.model}</dd>
        {record.reused_from && (
          <>
            <dt>生成来源</dt>
            <dd>JD、简历和要求未变，沿用已有招呼</dd>
          </>
        )}
        {!!record.usage?.total_tokens && (
          <>
            <dt>模型用量</dt>
            <dd>{record.usage.total_tokens.toLocaleString()} tokens</dd>
          </>
        )}
      </dl>
      <h3>完整招呼</h3>
      <div className="greeting-text">
        {record.message || "尚未生成完整招呼"}
      </div>
      {(record.note || record.delivery_note) && (
        <p className="detail-note" role="status">
          {[record.note, record.delivery_note].filter(Boolean).join("；")}
        </p>
      )}
      <details>
        <summary>当时的岗位 JD</summary>
        <p className="greeting-text">
          {record.job_json.description || "未读取到岗位 JD"}
        </p>
      </details>
      {record.evidence_json?.length > 0 && (
        <details>
          <summary>招呼中的简历依据</summary>
          {record.evidence_json.map((item, index) => (
            <div key={index}>
              <h4>{item.anchor}</h4>
              <p className="greeting-text">{item.quote}</p>
            </div>
          ))}
        </details>
      )}
      <details>
        <summary>当时的简历特点</summary>
        <p>{record.profile_json.summary}</p>
        {[
          ["skills", "技能"],
          ["experiences", "经历"],
          ["strengths", "优势"],
        ].map(
          ([key, label]) =>
            record.profile_json[key]?.length > 0 && (
              <div key={key}>
                <h4>{label}</h4>
                <ul>
                  {record.profile_json[key].map((fact, i) => (
                    <li key={i}>{fact}</li>
                  ))}
                </ul>
              </div>
            ),
        )}
      </details>
      <details>
        <summary>当时的招呼要求</summary>
        <p className="greeting-text">
          {record.instructions || "按岗位与简历自动组织表达"}
        </p>
      </details>
    </dialog>
  );
}

export default function GreetingHistory({ overview }) {
  const [data, setData] = useState({ items: [], nextCursor: null });
  const [cursors, setCursors] = useState([0]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const cursor = cursors.at(-1);
  const revision = JSON.stringify(overview);
  useEffect(() => {
    let current = true;
    setLoading(true);
    window.desk
      .greetings({ cursor })
      .then((value) => {
        if (current) {
          setData(value);
          setError("");
        }
      })
      .catch((err) => {
        if (current) setError(err.message);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [cursor, revision, reload]);
  useEffect(() => {
    let current = true;
    if (selectedId)
      window.desk
        .greetings({ id: selectedId })
        .then((value) => {
          if (current) setDetail(value);
        })
        .catch((err) => {
          if (current) setError(err.message);
        });
    else setDetail(null);
    return () => {
      current = false;
    };
  }, [selectedId, revision, reload]);
  return (
    <>
      <div className="page-heading">
        <h1>招呼记录</h1>
        <button
          className="btn"
          onClick={() => {
            setCursors([0]);
            setReload((n) => n + 1);
          }}
        >
          <RefreshCw size={15} /> 刷新记录
        </button>
      </div>
      <p className="greeting-history-intro">
        自动保存每个岗位的招呼、生成依据与投递结果。
      </p>
      <section className="panel" aria-busy={loading}>
        <div className="table-toolbar">
          <span>共 {overview?.total || 0} 条记录</span>
          <span>第 {cursors.length} 页</span>
        </div>
        {error && (
          <p className="ai-warning" role="alert">
            {error}
          </p>
        )}
        {data.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>职位 / 公司</th>
                  <th>发现方式</th>
                  <th>结果</th>
                  <th>时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <b>{row.title}</b>
                      <small>{row.company}</small>
                    </td>
                    <td>{row.mode === "preview" ? "预览职位" : "正式投递"}</td>
                    <td>
                      <span
                        className={
                          "badge " + (row.delivery_status || row.status)
                        }
                      >
                        {result(row)}
                      </span>
                      {row.reused_from && <small>沿用已有招呼</small>}
                    </td>
                    <td>{time(row.created_at)}</td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => setSelectedId(row.id)}
                      >
                        查看记录
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            <div className="empty-icon">
              <History size={26} />
            </div>
            <h3>{loading ? "正在读取记录…" : "暂无招呼记录"}</h3>
            <p>开启 AI 岗位招呼后，预览或投递时会自动生成并保存。</p>
          </div>
        )}
        {(cursors.length > 1 || data.nextCursor) && (
          <div className="greeting-pagination">
            <button
              className="btn"
              disabled={loading || cursors.length === 1}
              onClick={() => setCursors((values) => values.slice(0, -1))}
            >
              上一页
            </button>
            <button
              className="btn"
              disabled={loading || !data.nextCursor}
              onClick={() =>
                setCursors((values) => [...values, data.nextCursor])
              }
            >
              下一页
            </button>
          </div>
        )}
      </section>
      {detail && selectedId && (
        <GreetingDetail
          key={detail.id}
          record={detail}
          close={() => setSelectedId(null)}
        />
      )}
    </>
  );
}
