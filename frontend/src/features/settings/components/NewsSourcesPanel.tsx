import { useId, useState } from "react";
import { AsyncStateView, Button, Modal } from "@/components/ui";
import type { ApiError } from "@/types";
import { NEWS_SOURCE_TYPE_LABELS, NEWS_SOURCE_TYPES } from "../constants";
import type { NewsSource, NewsSourceType } from "../types";
import { useNewsSourceMutations, useNewsSources } from "../useSettings";

const EMPTY: NewsSource = {
  source_id: "",
  name: "",
  source_type: "rss",
  category: "",
  enabled: true,
  config_json: { url: "" },
};

export function NewsSourcesPanel() {
  const { data, status, error, refetch } = useNewsSources();
  const m = useNewsSourceMutations();

  const [editing, setEditing] = useState<NewsSource | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [testResult, setTestResult] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const uid = useId();

  const sources = data?.sources ?? [];

  function openAdd() {
    setEditing({ ...EMPTY });
    setIsNew(true);
    setIsOpen(true);
    setTestResult(null);
  }

  function openEdit(src: NewsSource) {
    setEditing({ ...src });
    setIsNew(false);
    setIsOpen(true);
    setTestResult(null);
  }

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading news sources…" />;
  }
  if (status === "error") {
    return <AsyncStateView status="error" error={error as ApiError} onRetry={() => refetch()} />;
  }

  async function onSave() {
    if (!editing) return;
    if (!editing.source_id.trim() || !editing.name.trim()) {
      setTestResult({ kind: "err", text: "Source ID and Name are required." });
      return;
    }
    const cfg =
      editing.source_type === "rss"
        ? { url: String(editing.config_json?.url ?? "") }
        : { subreddit: String(editing.config_json?.subreddit ?? "") };
    const payload: NewsSource = {
      ...editing,
      source_id: editing.source_id.trim(),
      name: editing.name.trim(),
      category: editing.category?.trim() || undefined,
      config_json: cfg,
    };
    setBusy(true);
    try {
      if (isNew) {
        await m.create(payload);
      } else {
        await m.update({ id: payload.source_id, source: payload });
      }
      setIsOpen(false);
      setEditing(null);
    } catch (e) {
      setTestResult({
        kind: "err",
        text: e instanceof Error ? e.message : "Save failed.",
      });
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    if (!editing) return;
    const cfg =
      editing.source_type === "rss"
        ? { url: String(editing.config_json?.url ?? "") }
        : { subreddit: String(editing.config_json?.subreddit ?? "") };
    setBusy(true);
    setTestResult(null);
    try {
      const res = (await m.test({
        source_type: editing.source_type,
        config_json: cfg,
      })) as { reachable?: boolean; message?: string; sample_titles?: string[] };
      if (res.reachable) {
        setTestResult({
          kind: "ok",
          text: `✓ ${res.message ?? "OK"}${res.sample_titles?.[0] ? ` — "${res.sample_titles[0]}"` : ""}`,
        });
      } else {
        setTestResult({ kind: "err", text: `✗ ${res.message ?? "Test failed"}` });
      }
    } catch (e) {
      setTestResult({
        kind: "err",
        text: e instanceof Error ? e.message : "Test failed.",
      });
    } finally {
      setBusy(false);
    }
  }

  const type = editing?.source_type ?? "rss";

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>News Sources</h2>
        <Button className="btn btn-trailing" onClick={openAdd}>
          Add Source
        </Button>
      </div>
      <div className="table-scroll">
        <table className="data-table table-compact">
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Type</th>
              <th>Category</th>
              <th>Status</th>
              <th>Config</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {sources.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-row">
                  No sources configured
                </td>
              </tr>
            ) : (
              sources.map((s) => (
                <tr key={s.source_id}>
                  <td className="source-id">{s.source_id}</td>
                  <td>{s.name}</td>
                  <td>
                    {NEWS_SOURCE_TYPE_LABELS[s.source_type as NewsSourceType] ?? s.source_type}
                  </td>
                  <td>{s.category ?? "—"}</td>
                  <td>
                    {s.enabled ? (
                      <span className="news-status-on">ON</span>
                    ) : (
                      <span className="news-status-off">OFF</span>
                    )}
                  </td>
                  <td className="source-id source-summary">
                    {s.source_type === "rss"
                      ? (s.config_json?.url ?? "").slice(0, 50)
                      : `r/${s.config_json?.subreddit ?? ""}`}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="news-action-btn"
                      onClick={() => m.setEnabled({ id: s.source_id, enabled: !s.enabled })}
                    >
                      {s.enabled ? "Disable" : "Enable"}
                    </button>{" "}
                    <button type="button" className="news-action-btn" onClick={() => openEdit(s)}>
                      Edit
                    </button>{" "}
                    <button
                      type="button"
                      className="news-action-btn danger"
                      onClick={() => m.remove(s.source_id)}
                    >
                      Del
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {isOpen && editing ? (
        <Modal
          label={isNew ? "Add news source" : "Edit news source"}
          onClose={() => setIsOpen(false)}
        >
          <div className="modal-card">
            <div className="modal-header">
              <h3>{isNew ? "Add Source" : "Edit Source"}</h3>
              <button type="button" className="icon-btn" onClick={() => setIsOpen(false)}>
                ×
              </button>
            </div>
            <div className="modal-body">
              <div className="form-row">
                <label htmlFor={`${uid}-src-id`}>Source ID</label>
                <input
                  id={`${uid}-src-id`}
                  className="filter-input"
                  value={editing.source_id}
                  disabled={!isNew}
                  onChange={(e) => setEditing({ ...editing, source_id: e.target.value })}
                />
              </div>
              <div className="form-row">
                <label htmlFor={`${uid}-src-name`}>Name</label>
                <input
                  id={`${uid}-src-name`}
                  className="filter-input"
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                />
              </div>
              <div className="form-row">
                <label htmlFor={`${uid}-src-type`}>Type</label>
                <select
                  id={`${uid}-src-type`}
                  className="filter-input"
                  value={type}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      source_type: e.target.value as NewsSourceType,
                    })
                  }
                >
                  {NEWS_SOURCE_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {NEWS_SOURCE_TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-row">
                <label htmlFor={`${uid}-src-cat`}>Category</label>
                <input
                  id={`${uid}-src-cat`}
                  className="filter-input"
                  value={editing.category ?? ""}
                  onChange={(e) => setEditing({ ...editing, category: e.target.value })}
                />
              </div>
              {type === "rss" ? (
                <div className="form-row">
                  <label htmlFor={`${uid}-src-url`}>RSS URL</label>
                  <input
                    id={`${uid}-src-url`}
                    className="filter-input"
                    value={editing.config_json?.url ?? ""}
                    onChange={(e) =>
                      setEditing({
                        ...editing,
                        config_json: { ...editing.config_json, url: e.target.value },
                      })
                    }
                  />
                </div>
              ) : (
                <div className="form-row">
                  <label htmlFor={`${uid}-src-sub`}>Subreddit</label>
                  <input
                    id={`${uid}-src-sub`}
                    className="filter-input"
                    value={editing.config_json?.subreddit ?? ""}
                    onChange={(e) =>
                      setEditing({
                        ...editing,
                        config_json: { ...editing.config_json, subreddit: e.target.value },
                      })
                    }
                  />
                </div>
              )}
              <div className="form-row">
                <span className="auth-label" aria-hidden="true" />
                <Button onClick={onTest} disabled={busy}>
                  Test Source
                </Button>
                {testResult ? (
                  <span className={`hint news-test-result ${testResult.kind}`}>
                    {testResult.text}
                  </span>
                ) : null}
              </div>
            </div>
            <div className="modal-footer">
              <Button onClick={() => setIsOpen(false)}>Cancel</Button>
              <Button className="btn accent" onClick={onSave} disabled={busy}>
                Save
              </Button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
