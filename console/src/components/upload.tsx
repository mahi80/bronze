"use client";

// Load a delivered CSV into the pipeline from the browser. The file lands in the uploads directory and the
// next run ingests it through the same bronze path as data/.
import { FileUp } from "lucide-react";
import { useRef, useState } from "react";
import { errorMessage } from "./api";

export function UploadButton({ onChanged }: { onChanged: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<{ busy: boolean; text?: string; error?: string }>({ busy: false });

  async function send(file: File) {
    setState({ busy: true });
    try {
      const form = new FormData();
      form.set("file", file);
      const response = await fetch("/api/upload", { method: "POST", body: form, cache: "no-store" });
      const data: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = data && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string"
          ? (data as { detail: string }).detail : "The upload did not complete.";
        throw new Error(detail);
      }
      const saved = (data as { file?: string }).file ?? file.name;
      setState({ busy: false, text: `Uploaded as ${saved}. Run the pipeline to ingest it.` });
      onChanged();
    } catch (failure) {
      setState({ busy: false, error: errorMessage(failure) });
    } finally {
      if (input.current) input.current.value = "";
    }
  }

  return (
    <>
      <input ref={input} type="file" accept=".csv,text/csv" className="file-input" aria-label="Choose a CSV to load"
        onChange={(event) => { const file = event.target.files?.[0]; if (file) void send(file); }} />
      <button className="button secondary" disabled={state.busy} onClick={() => input.current?.click()}>
        <FileUp size={15} /> {state.busy ? "Uploading" : "Load a CSV"}
      </button>
      {(state.text || state.error) && (
        <p className={`upload-note ${state.error ? "error" : ""}`} role="status">{state.error ?? state.text}</p>
      )}
    </>
  );
}
