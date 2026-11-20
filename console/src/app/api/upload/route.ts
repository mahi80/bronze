import { NextResponse } from "next/server";
import { ConsoleError, requireSameOrigin } from "@/lib/request-policy";
import { backendUpload, configuration, errorResponse, PRIVATE_HEADERS } from "@/lib/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_BYTES = 20_000_000;

// The browser uploads a delivered CSV here; this route checks it and forwards it to the pipeline API.
export async function POST(request: Request) {
  try {
    requireSameOrigin(request, configuration().origins);
    const declared = Number(request.headers.get("content-length") || "0");
    if (!declared || declared > MAX_BYTES + 100_000) throw new ConsoleError(413, "A CSV of at most 20 MB is accepted.");
    const form = await request.formData();
    const file = form.get("file");
    if (!(file instanceof File)) throw new ConsoleError(400, "Choose a CSV file to upload.");
    if (!file.name.toLowerCase().endsWith(".csv")) throw new ConsoleError(400, "Only .csv files are accepted.");
    if (file.size === 0) throw new ConsoleError(400, "The file is empty.");
    if (file.size > MAX_BYTES) throw new ConsoleError(413, "A CSV of at most 20 MB is accepted.");
    const forwarded = new FormData();
    forwarded.set("file", file, file.name);
    return NextResponse.json(await backendUpload(forwarded), { headers: PRIVATE_HEADERS });
  } catch (error) {
    return errorResponse(error);
  }
}
