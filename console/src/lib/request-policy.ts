// What the browser may ask the console's server to forward to the pipeline API, and how request bodies are read.
// Adapted from the request policy of my DataMigration console.

export class ConsoleError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ConsoleError";
    this.status = status;
  }
}

/** A request that changes something must come from one of the console's own pages. */
export function requireSameOrigin(request: Request, allowedOrigins: string[]): void {
  const origin = request.headers.get("origin");
  if (!origin || !allowedOrigins.includes(origin)) {
    throw new ConsoleError(403, "This request must come from the console itself. If you open the console under another address, add it to CONSOLE_ORIGIN.");
  }
  const site = request.headers.get("sec-fetch-site");
  if (site && !["same-origin", "none"].includes(site)) {
    throw new ConsoleError(403, "Cross-site requests are not accepted.");
  }
}

const READ = /^(health|overview|runs|runs\/\d{1,12}|quality|data\/profile|data\/rows|gold\/[a-z_]{1,40}|tickets\/[A-Za-z0-9-]{1,40}|review|agents|agents\/job)$/;
const WRITE = /^(runs|review\/mappings|review\/rules|agents\/(classify|dq))$/;

/** Turn a browser request into an API path, or refuse. Only the routes the console uses are forwarded. */
export function backendTarget(method: string, path: string[], query: URLSearchParams): string {
  const joined = path.join("/");
  if (!((method === "GET" && READ.test(joined)) || (method === "POST" && WRITE.test(joined)))) {
    throw new ConsoleError(404, "This console operation is not available.");
  }
  const allowed = method === "GET" && (joined === "runs" || joined === "data/rows") ? ["limit"] : [];
  const clean = new URLSearchParams();
  for (const [key, value] of query) {
    if (!allowed.includes(key) || clean.has(key) || !/^\d{1,4}$/.test(value)) {
      throw new ConsoleError(400, "Unsupported request parameters.");
    }
    clean.set(key, value);
  }
  return joined + (clean.size ? `?${clean.toString()}` : "");
}

/** Read a JSON body with a size limit, without trusting Content-Length. */
export async function readJsonBody(input: Request | Response, maxBytes = 1_000_000): Promise<unknown> {
  if (!input.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    throw new ConsoleError(415, "A JSON request is required.");
  }
  const reader = input.body?.getReader();
  if (!reader) throw new ConsoleError(400, "A JSON body is required.");
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      length += part.value.byteLength;
      if (length > maxBytes) {
        await reader.cancel();
        throw new ConsoleError(413, "The request exceeds the console size limit.");
      }
      chunks.push(part.value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let position = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, position);
    position += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new ConsoleError(400, "Invalid JSON content.");
  }
}
