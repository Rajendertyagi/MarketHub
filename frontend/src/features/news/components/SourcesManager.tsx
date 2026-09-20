import { useState } from "react";
import { AsyncStateView, Button, Field, Input, Select } from "@/components/ui";
import type { ApiError } from "@/types";
import { NEWS_SOURCE_TYPES } from "../constants";
import type { NewsSource, NewsSourceInput, NewsSourceType } from "../types";
import { useNewsMutations, useNewsSources } from "../useNews";

interface DraftState {
  source_id: string;
  name: string;
  source_type: NewsSourceType;
  category: string;
  enabled: boolean;
  config_text: string;
}

const EMPTY_DRAFT: DraftState = {
  source_id: "",
  name: "",
  source_type: "rss",
  category: "",
  enabled: true,
  config_text: JSON.stringify({ url: "" }, null, 2),
};

export function SourcesManager() {
  const query = useNewsSources();
  const m = useNewsMutations();
  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);

  if (query.isLoading) {
    return <AsyncStateView status="loading" loadingLabel="Loading sources…" />;
  }
  if (query.isError) {
    return (
      <AsyncStateView
        status="error"
        error={query.error as ApiError}
        onRetry={() => query.refetch()}
      />
    );
  }

  const sources = query.data?.sources ?? [];

  const parseConfig = (): Record<string, unknown> | null => {
    try {
      const parsed = JSON.parse(draft.config_text);
      return typeof parsed === "object" && parsed !== null
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  };

  const submit = async () => {
    setFormError(null);
    setTestMsg(null);
    const config_json = parseConfig();
    if (!config_json) {
      setFormError("config_json must be valid JSON");
      return;
    }
    if (!draft.source_id.trim() || !draft.name.trim()) {
      setFormError("source_id and name are required");
      return;
    }
    const input: NewsSourceInput = {
      source_id: draft.source_id.trim(),
      name: draft.name.trim(),
      source_type: draft.source_type,
      category: draft.category.trim(),
      enabled: draft.enabled,
      config_json,
    };
    if (editingId) {
      await m.updateSource({ id: editingId, input });
    } else {
      await m.createSource(input);
    }
    setDraft(EMPTY_DRAFT);
    setEditingId(null);
  };

  const startEdit = (src: NewsSource) => {
    setEditingId(src.source_id);
    setFormError(null);
    setTestMsg(null);
    setDraft({
      source_id: src.source_id,
      name: src.name,
      source_type: src.source_type,
      category: src.category,
      enabled: src.enabled,
      config_text: JSON.stringify(src.config_json, null, 2),
    });
  };

  const runTest = async () => {
    setTestMsg(null);
    const config_json = parseConfig();
    if (!config_json) {
      setFormError("config_json must be valid JSON before testing");
      return;
    }
    try {
      const res = (await m.testSource({
        type: draft.source_type,
        config: config_json,
      })) as {
        status: string;
        reachable?: boolean;
        message?: string;
      };
      setTestMsg(`${res.status}${res.reachable ? " · reachable" : ""} — ${res.message ?? ""}`);
    } catch (e) {
      setTestMsg(e instanceof Error ? e.message : "test failed");
    }
  };

  return (
    <div className="sources-manager">
      <div className="card">
        <h3>{editingId ? `Edit source: ${editingId}` : "Add news source"}</h3>
        <div className="control-row">
          <Field label="Source ID">
            <Input
              value={draft.source_id}
              disabled={editingId !== null}
              placeholder="my-rss-feed"
              onChange={(e) => setDraft({ ...draft, source_id: e.target.value })}
            />
          </Field>
          <Field label="Name">
            <Input
              value={draft.name}
              placeholder="My RSS Feed"
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </Field>
          <Field label="Type">
            <Select
              value={draft.source_type}
              onChange={(e) =>
                setDraft({ ...draft, source_type: e.target.value as NewsSourceType })
              }
            >
              {NEWS_SOURCE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Category">
            <Input
              value={draft.category}
              placeholder="general"
              onChange={(e) => setDraft({ ...draft, category: e.target.value })}
            />
          </Field>
        </div>
        <Field label="config_json">
          <textarea
            className="input source-config"
            value={draft.config_text}
            onChange={(e) => setDraft({ ...draft, config_text: e.target.value })}
          />
        </Field>
        {formError ? <p className="hint err">{formError}</p> : null}
        <div className="control-row">
          <label className="switch">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
            />
            <span>Enabled</span>
          </label>
          <Button variant="primary" onClick={submit}>
            {editingId ? "Save" : "Add"}
          </Button>
          <Button variant="default" onClick={runTest}>
            Test
          </Button>
          {editingId ? (
            <Button
              variant="default"
              onClick={() => {
                setDraft(EMPTY_DRAFT);
                setEditingId(null);
                setFormError(null);
                setTestMsg(null);
              }}
            >
              Cancel
            </Button>
          ) : null}
        </div>
        {testMsg ? <p className="hint ok">{testMsg}</p> : null}
      </div>

      <div className="card">
        <h3>Sources ({sources.length})</h3>
        {sources.length === 0 ? (
          <span className="muted">No news sources configured.</span>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Enabled</th>
                <th>Name</th>
                <th>Type</th>
                <th>Category</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sources.map((src) => (
                <tr key={src.source_id}>
                  <td>
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={src.enabled}
                        onChange={(e) =>
                          m.setSourceEnabled({
                            id: src.source_id,
                            enabled: e.target.checked,
                          })
                        }
                      />
                      <span />
                    </label>
                  </td>
                  <td>{src.name}</td>
                  <td>{src.source_type}</td>
                  <td className="muted">{src.category || "—"}</td>
                  <td>
                    <Button variant="default" onClick={() => startEdit(src)}>
                      Edit
                    </Button>
                    <Button variant="default" onClick={() => m.deleteSource(src.source_id)}>
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
