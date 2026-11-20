import "server-only";
import { NextResponse } from "next/server";
import { ConsoleError, readJsonBody } from "./request-policy";

export const PRIVATE_HEADERS = { "Cache-Control": "no-store, private" };

/** Server-side settings. The API token stays here: it is never sent to the browser. */
export function configuration() {
  const token = process.env.CONSOLE_API_TOKEN || "";
  if (!token) {
    throw new ConsoleError(503, "The console is not configured: set CONSOLE_API_TOKEN to the value the API was started with.");
  }
  const origins = (process.env.CONSOLE_ORIGIN || "http://localhost:3000,http://127.0.0.1:3000").split(",").map((origin) => origin.trim()).filter(Boolean);
  return { api: (process.env.PIPELINE_API_URL || "http://127.0.0.1:8000").replace(/\/$/, ""), token, origins };
}

export async function backendRequest(target: string, method = "GET", body?: unknown): Promise<unknown> {
  const config = configuration();
  let response: Response;
  try {
    response = await fetch(`${config.api}/${target}`, {
      method,
      cache: "no-store",
      redirect: "error",
      headers: { Authorization: `Bearer ${config.token}`, Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(90_000),
    });
  } catch {
    throw new ConsoleError(502, method === "GET"
      ? "The pipeline API could not be reached. Check that it is running, then refresh."
      : "The API did not confirm this action. Check the latest run or the review queue before trying again.");
  }
  let data: unknown;
  try {
    data = await readJsonBody(response, 8_000_000);
  } catch {
    throw new ConsoleError(502, "The pipeline API returned an unexpected response.");
  }
  if (!response.ok) {
    const detail = data && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string" ? (data as { detail: string }).detail : undefined;
    const message = response.status === 401 ? "The console's API token was not accepted. CONSOLE_API_TOKEN must match on the console and the API."
      : detail?.slice(0, 1000) || "The pipeline API could not complete this request.";
    throw new ConsoleError(response.status >= 400 && response.status < 500 ? response.status : 502, message);
  }
  return data;
}

export function errorResponse(error: unknown) {
  const safe = error instanceof ConsoleError ? error : new ConsoleError(500, "The console could not complete this request.");
  return NextResponse.json({ detail: safe.message }, { status: safe.status, headers: PRIVATE_HEADERS });
}
