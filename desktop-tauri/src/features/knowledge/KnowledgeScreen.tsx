import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { chunksLabel, searchKind, statusColor } from "../../api/format";
import { useCollections, useDocuments } from "../../api/queries";
import type { RetrievedChunk } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, Dialog, IconButton, Spinner, TextField, useConfirm } from "../../components/ui";
import { useToast } from "../../state/toast";

export function KnowledgeScreen() {
  const collections = useCollections();
  const queries = useQueryClient();
  const toast = useToast();
  const { confirm, element } = useConfirm();
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null);
  const [creating, setCreating] = useState(false);

  const remove = async (id: string, name: string) => {
    const ok = await confirm({
      title: "Delete collection?",
      message: `"${name}" and all its documents and vectors will be permanently deleted. This cannot be undone.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteCollection(id);
      if (selected?.id === id) setSelected(null);
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      void queries.invalidateQueries({ queryKey: ["collections"] });
    }
  };

  return (
    <div className="page" style={{ height: "100%" }}>
      <div className="split">
        <div className="col" style={{ minHeight: 0 }}>
          <div className="row">
            <h2>Collections</h2>
            <span className="spacer" />
            <IconButton icon="add" label="New collection" onClick={() => setCreating(true)} />
          </div>
          <div style={{ overflow: "auto" }}>
            {collections.isPending ? (
              <Spinner />
            ) : collections.isError ? (
              <p className="error-text">Failed: {friendly(collections.error)}</p>
            ) : collections.data.length === 0 ? (
              <p className="muted">No collections yet.</p>
            ) : (
              collections.data.map((c) => (
                <div
                  key={c.id}
                  className={`list-item clickable ${selected?.id === c.id ? "selected" : ""}`}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selected?.id === c.id}
                  onClick={() => setSelected({ id: c.id, name: c.name })}
                >
                  <Icon name="folder" />
                  <div className="grow" style={{ minWidth: 0 }}>
                    <div className="ellipsis">{c.name}</div>
                    <div className="small muted">{[c.description || null, searchKind(c.embedding_backend)].filter(Boolean).join(" · ")}</div>
                  </div>
                  <IconButton
                    icon="delete"
                    label="Delete collection"
                    onClick={(e) => {
                      e.stopPropagation();
                      void remove(c.id, c.name);
                    }}
                  />
                </div>
              ))
            )}
          </div>
        </div>
        <div style={{ minHeight: 0, minWidth: 0 }}>
          {selected ? (
            <CollectionDetail key={selected.id} id={selected.id} name={selected.name} />
          ) : (
            <div className="fill-center muted">Select a collection to see its documents.</div>
          )}
        </div>
      </div>
      {creating && <CreateCollectionDialog onClose={() => setCreating(false)} />}
      {element}
    </div>
  );
}

function CreateCollectionDialog({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    if (!name.trim()) return;
    try {
      await api.createCollection(name.trim(), description.trim() || null);
      void queries.invalidateQueries({ queryKey: ["collections"] });
      onClose();
    } catch (e) {
      setError(friendly(e));
    }
  };
  return (
    <Dialog
      title="New collection"
      onClose={onClose}
      width={420}
      actions={
        <>
          <Button variant="text" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void create()}>Create</Button>
        </>
      }
    >
      <div className="stack-8">
        <TextField label="Name" value={name} onChange={setName} placeholder="flutter-docs" autoFocus onEnter={() => void create()} />
        <TextField label="Description (optional)" value={description} onChange={setDescription} />
        {error && <p className="error-text">{error}</p>}
      </div>
    </Dialog>
  );
}

function CollectionDetail({ id, name }: { id: string; name: string }) {
  const documents = useDocuments(id);
  const queries = useQueryClient();
  const toast = useToast();
  const [uploading, setUploading] = useState(false);

  const upload = async () => {
    setUploading(true);
    try {
      const doc = await api.uploadDocument(id);
      if (doc) {
        toast.show(
          doc.status === "embedded"
            ? `${doc.filename}: embedded as ${chunksLabel(doc.chunk_count)}`
            : `${doc.filename}: ${doc.status} — ${doc.error ?? ""}`,
          doc.status === "embedded" ? "info" : "error",
        );
      }
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      // failed documents are recorded with their error, so refresh anyway
      void queries.invalidateQueries({ queryKey: ["collections", id, "documents"] });
      setUploading(false);
    }
  };

  return (
    <div className="col" style={{ height: "100%", gap: 8 }}>
      <div className="row">
        <h1 className="grow ellipsis">{name}</h1>
        <Button icon="upload" busy={uploading} onClick={() => void upload()}>
          Upload document
        </Button>
      </div>
      <div style={{ flex: 2, overflow: "auto", minHeight: 80 }}>
        {documents.isPending ? (
          <Spinner />
        ) : documents.isError ? (
          <p className="error-text">Failed: {friendly(documents.error)}</p>
        ) : documents.data.length === 0 ? (
          <p className="muted center" style={{ padding: 16 }}>
            No documents in this collection.
          </p>
        ) : (
          documents.data.map((d) => (
            <div key={d.id} className="list-item">
              <span style={{ color: statusColor(d.status) }}>
                <Icon name={d.status === "embedded" ? "check" : d.status === "failed" ? "warning" : "pause"} />
              </span>
              <div className="grow">
                <div>{d.filename}</div>
                <div className="small muted">
                  {d.status === "embedded" ? `${chunksLabel(d.chunk_count)} · ${(d.size_bytes / 1024).toFixed(1)} KB` : (d.error ?? d.status)}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
      <hr style={{ border: 0, borderTop: "1px solid var(--border)", width: "100%" }} />
      <div style={{ flex: 3, minHeight: 0, display: "flex", flexDirection: "column" }}>
        <Playground collection={name} />
      </div>
    </div>
  );
}

function Playground({ collection }: { collection: string }) {
  const [query, setQuery] = useState("");
  const [scoped, setScoped] = useState(true);
  const [busy, setBusy] = useState(false);
  const [chunks, setChunks] = useState<RetrievedChunk[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setChunks(await api.retrieve(query.trim(), scoped ? collection : null));
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="col" style={{ gap: 8, minHeight: 0, flex: 1 }}>
      <h2>Retrieval playground</h2>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div className="grow">
          <TextField label="Query" value={query} onChange={setQuery} onEnter={() => void search()} />
        </div>
        <label className="checkbox" title="Search only this collection (otherwise the router searches all collections)">
          <input type="checkbox" checked={scoped} onChange={(e) => setScoped(e.target.checked)} />
          scoped
        </label>
        <Button busy={busy} onClick={() => void search()}>
          Retrieve
        </Button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <div style={{ overflow: "auto", flex: 1 }}>
        {chunks !== null &&
          (chunks.length === 0 ? (
            <p className="muted">No results.</p>
          ) : (
            chunks.map((c, i) => (
              <div key={i} className="card row top" style={{ padding: 10, marginBottom: 6 }}>
                <span className="mono">{c.score.toFixed(2)}</span>
                <div className="grow" style={{ minWidth: 0 }}>
                  <div style={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{c.text}</div>
                  <div className="small muted">
                    {c.collection}/{c.source}
                  </div>
                </div>
              </div>
            ))
          ))}
      </div>
    </div>
  );
}
